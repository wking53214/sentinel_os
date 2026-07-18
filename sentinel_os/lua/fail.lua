-- ARGV: 1 prefix, 2 job_id, 3 worker_id, 4 claim_id, 5 reason, 6 detail,
--       7 retryable('1'|'0'), 8 base_backoff_ms, 9 max_backoff_ms,
--       10 jitter_ms, 11 error_trail_keep
-- Every failure records WHY (reason + detail + attempt + worker + time)
-- before the job moves; exhausted or non-retryable jobs dead-letter.
local prefix, id = ARGV[1], ARGV[2]
local ok = owner_check(prefix, id, ARGV[3], ARGV[4])
if ok ~= 'ok' then
  -- UNIFIED: a fail() against an already-completed job answers 'gone',
  -- exactly as v1 did when completion deleted the hash.
  if ok == 'stale' and redis.call('HGET', prefix .. ':job:' .. id, 'status') == 'done' then
    return 'gone'
  end
  redis.call('HINCRBY', prefix .. ':counters', 'stale_fails', 1)
  return ok
end
local jkey = prefix .. ':job:' .. id
local now = now_ms()
local attempts = tonumber(redis.call('HGET', jkey, 'attempts'))
local max_attempts = tonumber(redis.call('HGET', jkey, 'max_attempts'))
record_error(prefix, id, attempts, ARGV[5], ARGV[6], ARGV[3], now, tonumber(ARGV[11]))
redis.call('ZREM', prefix .. ':processing', id)
redis.call('HDEL', jkey, 'claimed_by', 'claim_id', 'claimed_at_ms', 'lease_deadline_ms')
if ARGV[7] ~= '1' or attempts >= max_attempts then
  local esc = 0
  if ARGV[5] == 'unclassified' then esc = 1 end
  redis.call('HSET', jkey, 'status', 'dead', 'dead_at_ms', now,
             'dead_reason', ARGV[5], 'escalate', esc)
  redis.call('ZADD', prefix .. ':dead', now, id)
  redis.call('HINCRBY', prefix .. ':counters', 'dead', 1)
  redis.call('HINCRBY', prefix .. ':dead_reasons', ARGV[5], 1)
  return 'dead'
end
local backoff = math.min(tonumber(ARGV[8]) * 2 ^ (attempts - 1), tonumber(ARGV[9]))
backoff = math.floor(backoff + tonumber(ARGV[10]))
redis.call('HSET', jkey, 'status', 'scheduled', 'next_retry_at_ms', now + backoff)
redis.call('ZADD', prefix .. ':scheduled', now + backoff, id)
redis.call('HINCRBY', prefix .. ':counters', 'retries', 1)
return 'scheduled:' .. backoff
