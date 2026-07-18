-- rate_limit.lua : atomic token-bucket check-and-consume.
-- KEYS[1] = bucket key (e.g. "rl:<namespace>:<principal>")
-- ARGV[1] = capacity (max burst, integer tokens)
-- ARGV[2] = refill_rate_per_ms (tokens added per millisecond, float)
-- ARGV[3] = cost (tokens this request consumes, normally 1)
-- ARGV[4] = ttl_ms (bucket key expiry -- bounds memory for principals
--           that stop sending traffic; refilled/recreated on next hit)
--
-- Clock authority: Redis server TIME, same convention as
-- lua/_common.lua's now_ms() -- never a client/worker host clock, so
-- distributed ingress replicas agree on one clock regardless of their
-- own clock skew.
--
-- Returns: {allowed (0/1), tokens_remaining (float, post-consume if
-- allowed / current if denied), retry_after_ms (0 if allowed, else
-- real time until 1 token is available)}
local function now_ms()
  local t = redis.call('TIME')
  return tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local ttl_ms = tonumber(ARGV[4])

local now = now_ms()
local bucket = redis.call('HMGET', key, 'tokens', 'updated_at_ms')
local tokens = tonumber(bucket[1])
local updated_at = tonumber(bucket[2])

if tokens == nil then
  -- First hit for this principal: bucket starts full.
  tokens = capacity
  updated_at = now
end

-- Refill for elapsed real time since last hit, capped at capacity.
local elapsed = math.max(0, now - updated_at)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed = 0
local retry_after_ms = 0

if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  local deficit = cost - tokens
  retry_after_ms = math.ceil(deficit / refill_rate)
end

redis.call('HSET', key, 'tokens', tostring(tokens), 'updated_at_ms', tostring(now))
redis.call('PEXPIRE', key, ttl_ms)

return {allowed, tostring(tokens), retry_after_ms}
