-- _common.lua : prepended to every operation script at load time.
-- Clock authority: Redis server TIME (single source; immune to worker clock skew).
-- Single-instance Redis assumed; keys are derived from the ARGV[1] prefix,
-- which is not cluster-slot safe (documented constraint).
local function now_ms()
  local t = redis.call('TIME')
  return tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end

local function record_error(prefix, id, attempt, reason, detail, worker, at_ms, keep)
  local rec = cjson.encode({attempt = attempt, reason = reason, detail = detail,
                            worker = worker, at_ms = at_ms})
  local ekey = prefix .. ':errors:' .. id
  redis.call('LPUSH', ekey, rec)
  redis.call('LTRIM', ekey, 0, keep - 1)
end

local function owner_check(prefix, id, worker, claim_id)
  -- fencing: 'ok' | 'gone' | 'stale'
  local jkey = prefix .. ':job:' .. id
  if redis.call('EXISTS', jkey) == 0 then return 'gone' end
  if redis.call('HGET', jkey, 'status') ~= 'processing' then return 'stale' end
  if redis.call('HGET', jkey, 'claimed_by') ~= worker then return 'stale' end
  if redis.call('HGET', jkey, 'claim_id') ~= claim_id then return 'stale' end
  return 'ok'
end
