-- ARGV: 1 prefix, 2 worker_id, 3 claim_id, 4 lease_ms, 5 promote_batch, 6 max_scan
-- Atomic: promotes due retries, pops exactly one job, stamps the lease.
-- Two concurrent callers can never receive the same job.
local prefix, worker, claim_id = ARGV[1], ARGV[2], ARGV[3]
local lease = tonumber(ARGV[4])
local now = now_ms()
local due = redis.call('ZRANGEBYSCORE', prefix .. ':scheduled', '-inf', now,
                       'LIMIT', 0, tonumber(ARGV[5]))
for i = 1, #due do
  redis.call('ZREM', prefix .. ':scheduled', due[i])
  redis.call('LPUSH', prefix .. ':pending', due[i])  -- back of the FIFO line
end
for _ = 1, tonumber(ARGV[6]) do
  local id = redis.call('RPOP', prefix .. ':pending')
  if not id then return nil end
  local jkey = prefix .. ':job:' .. id
  if redis.call('EXISTS', jkey) == 1 then
    local attempts = redis.call('HINCRBY', jkey, 'attempts', 1)
    local deadline = now + lease
    redis.call('HSET', jkey, 'status', 'processing', 'claimed_by', worker,
               'claim_id', claim_id, 'claimed_at_ms', now,
               'lease_deadline_ms', deadline)
    redis.call('ZADD', prefix .. ':processing', deadline, id)
    return {id,
            redis.call('HGET', jkey, 'payload'),
            redis.call('HGET', jkey, 'checksum'),
            tostring(attempts),
            redis.call('HGET', jkey, 'enqueued_at_ms'),
            tostring(deadline)}
  end
  -- dangling reference with no job hash: quarantine, never silently drop
  redis.call('LPUSH', prefix .. ':orphans', id)
  redis.call('HINCRBY', prefix .. ':counters', 'orphan_refs', 1)
end
return nil
