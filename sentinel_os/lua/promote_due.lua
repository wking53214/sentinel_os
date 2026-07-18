-- ARGV: 1 prefix, 2 batch
-- UNIFIED (cage match graft from Fighter R): promotion of due retries as
-- a distinct, explicitly callable operation. The structure transition is
-- the same one claim.lua's inline promotion performs (claim.lua itself
-- is unchanged); this standalone form additionally sets status='pending'
-- on the promoted hash -- Fighter R's promote_due semantics -- so a
-- promoted-but-unclaimed job reads consistently. Lets an operator or a
-- scheduler drain the scheduled set without claiming anything.
local prefix = ARGV[1]
local now = now_ms()
local due = redis.call('ZRANGEBYSCORE', prefix .. ':scheduled', '-inf', now,
                       'LIMIT', 0, tonumber(ARGV[2]))
for i = 1, #due do
  redis.call('ZREM', prefix .. ':scheduled', due[i])
  redis.call('LPUSH', prefix .. ':pending', due[i])  -- back of the FIFO line
  redis.call('HSET', prefix .. ':job:' .. due[i], 'status', 'pending')
end
return #due
