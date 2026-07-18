-- ARGV: 1 prefix, 2 job_id, 3 worker_id, 4 claim_id,
--       5 result_json ('' = none), 6 done_keep_ms (0 = legacy delete)
-- Fenced: a worker whose lease expired and whose job was reclaimed
-- gets 'stale' back and cannot complete someone else's claim.
-- UNIFIED (cage match graft): completion no longer deletes the job hash
-- outright -- it becomes a 'done' record (status='done', completed_at_ms,
-- optional result) that expires after done_keep_ms, tracked in a 'done'
-- zset scored by completion time. This is what makes a finished job
-- pollable ("done" view) and a post-completion resubmit a dedup instead
-- of a re-run. The fence, the counters, and every return value of the
-- original are preserved: a second ack of a completed job still returns
-- 'gone', exactly as before.
local prefix, id = ARGV[1], ARGV[2]
local jkey = prefix .. ':job:' .. id
local ok = owner_check(prefix, id, ARGV[3], ARGV[4])
if ok ~= 'ok' then
  if ok == 'stale' and redis.call('HGET', jkey, 'status') == 'done' then
    return 'gone'    -- second ack of a completed job: same contract as v1
  end
  redis.call('HINCRBY', prefix .. ':counters', 'stale_acks', 1)
  return ok
end
local now = now_ms()
redis.call('ZREM', prefix .. ':processing', id)
local keep = tonumber(ARGV[6])
if keep > 0 then
  redis.call('HDEL', jkey, 'claimed_by', 'claim_id', 'claimed_at_ms',
             'lease_deadline_ms')
  redis.call('HSET', jkey, 'status', 'done', 'completed_at_ms', now)
  if ARGV[5] ~= '' then
    redis.call('HSET', jkey, 'result', ARGV[5])
  end
  redis.call('PEXPIRE', jkey, keep)
  redis.call('PEXPIRE', prefix .. ':errors:' .. id, keep)
  redis.call('ZADD', prefix .. ':done', now, id)
  redis.call('ZREMRANGEBYSCORE', prefix .. ':done', '-inf', now - keep)
else
  -- done_keep_ms=0: the original v1 behavior, byte-for-byte effects.
  redis.call('DEL', jkey, prefix .. ':errors:' .. id)
end
redis.call('HINCRBY', prefix .. ':counters', 'completed', 1)
redis.call('HSET', prefix .. ':counters', 'last_completed_at_ms', now)
return 'ok'
