-- ARGV: 1 prefix, 2 job_id, 3 worker_id, 4 claim_id, 5 lease_ms
-- UNIFIED (cage match graft from Fighter R): lease renewal. Runs under
-- the SAME owner_check fence as ack/fail -- only the worker holding the
-- live claim (matching worker_id AND claim_id) can extend it. A reaped
-- or reclaimed worker gets 'stale'; a completed/vanished job gets
-- 'gone'. The processing zset score and the job hash deadline move
-- together, atomically, on the Redis clock.
local prefix, id = ARGV[1], ARGV[2]
local ok = owner_check(prefix, id, ARGV[3], ARGV[4])
if ok ~= 'ok' then
  if ok == 'stale' and redis.call('HGET', prefix .. ':job:' .. id, 'status') == 'done' then
    return 'gone'
  end
  redis.call('HINCRBY', prefix .. ':counters', 'stale_heartbeats', 1)
  return ok
end
local now = now_ms()
local deadline = now + tonumber(ARGV[5])
redis.call('HSET', prefix .. ':job:' .. id, 'lease_deadline_ms', deadline)
redis.call('ZADD', prefix .. ':processing', deadline, id)
redis.call('HINCRBY', prefix .. ':counters', 'heartbeats', 1)
return 'ok:' .. deadline
