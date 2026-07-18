-- ARGV: 1 prefix, 2 job_id, 3 payload_json, 4 checksum, 5 max_attempts
-- Idempotent on job_id: a duplicate enqueue is counted, not re-inserted.
-- UNIFIED (cage match graft): state logic is Fighter O's verbatim; the
-- return is enriched from 0/1 to {created, current_status} so callers
-- can echo live status on dedup (Fighter R's enqueue view). A job with a
-- retained 'done' record dedups like any live job -- a completed job is
-- never reset or re-run by a resubmit.
local prefix, id = ARGV[1], ARGV[2]
local jkey = prefix .. ':job:' .. id
if redis.call('EXISTS', jkey) == 1 then
  redis.call('HINCRBY', prefix .. ':counters', 'duplicate_enqueues', 1)
  return {0, redis.call('HGET', jkey, 'status')}
end
local now = now_ms()
redis.call('HSET', jkey,
  'id', id, 'payload', ARGV[3], 'checksum', ARGV[4],
  'status', 'pending', 'attempts', 0, 'max_attempts', ARGV[5],
  'enqueued_at_ms', now)
redis.call('LPUSH', prefix .. ':pending', id)
redis.call('HINCRBY', prefix .. ':counters', 'enqueued', 1)
return {1, 'pending'}
