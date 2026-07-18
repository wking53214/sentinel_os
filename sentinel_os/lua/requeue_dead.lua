-- ARGV: 1 prefix, 2 job_id  (operator action: give a dead job a fresh budget)
local prefix, id = ARGV[1], ARGV[2]
local jkey = prefix .. ':job:' .. id
if redis.call('EXISTS', jkey) == 0 then return 'gone' end
local st = redis.call('HGET', jkey, 'status')
-- UNIFIED: a completed job's retained record answers 'gone', as v1 did.
if st == 'done' then return 'gone' end
if st ~= 'dead' then return 'not_dead' end
redis.call('ZREM', prefix .. ':dead', id)
redis.call('HSET', jkey, 'status', 'pending', 'attempts', 0)
redis.call('HDEL', jkey, 'dead_at_ms', 'dead_reason', 'escalate')
redis.call('LPUSH', prefix .. ':pending', id)
redis.call('HINCRBY', prefix .. ':counters', 'requeued_from_dlq', 1)
return 'ok'
