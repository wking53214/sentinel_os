-- ARGV: 1 prefix, 2 batch, 3 error_trail_keep
-- Crash recovery from queue state alone: any processing entry whose
-- lease deadline has passed is recorded (reason: process_crash) and
-- requeued, or dead-lettered if its attempt budget is spent.
local prefix = ARGV[1]
local now = now_ms()
local expired = redis.call('ZRANGEBYSCORE', prefix .. ':processing', '-inf', now,
                           'LIMIT', 0, tonumber(ARGV[2]))
local out = {}
for i = 1, #expired do
  local id = expired[i]
  local jkey = prefix .. ':job:' .. id
  redis.call('ZREM', prefix .. ':processing', id)
  if redis.call('EXISTS', jkey) == 0 then
    redis.call('LPUSH', prefix .. ':orphans', id)
    redis.call('HINCRBY', prefix .. ':counters', 'orphan_refs', 1)
    out[#out + 1] = id .. ':orphan'
  else
    local attempts = tonumber(redis.call('HGET', jkey, 'attempts'))
    local max_attempts = tonumber(redis.call('HGET', jkey, 'max_attempts'))
    local who = redis.call('HGET', jkey, 'claimed_by') or 'unknown'
    record_error(prefix, id, attempts, 'process_crash',
                 'lease expired; worker ' .. tostring(who) .. ' presumed crashed or stalled',
                 tostring(who), now, tonumber(ARGV[3]))
    redis.call('HDEL', jkey, 'claimed_by', 'claim_id', 'claimed_at_ms', 'lease_deadline_ms')
    redis.call('HINCRBY', prefix .. ':counters', 'reaped', 1)
    if attempts >= max_attempts then
      redis.call('HSET', jkey, 'status', 'dead', 'dead_at_ms', now,
                 'dead_reason', 'process_crash', 'escalate', 0)
      redis.call('ZADD', prefix .. ':dead', now, id)
      redis.call('HINCRBY', prefix .. ':counters', 'dead', 1)
      redis.call('HINCRBY', prefix .. ':dead_reasons', 'process_crash', 1)
      out[#out + 1] = id .. ':dead'
    else
      redis.call('HSET', jkey, 'status', 'pending')
      redis.call('LPUSH', prefix .. ':pending', id)
      out[#out + 1] = id .. ':requeued'
    end
  end
end
return out
