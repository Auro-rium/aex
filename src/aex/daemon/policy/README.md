# policy - v2.0

Deterministic policy pipeline:
- kernel validation (`utils.policy_engine`) first
- optional plugin chain loaded from policy dir
- deny-first reducer with deterministic patch merge

Decision artifacts:
- `decision_hash`
- plugin trace
- obligations + constrained request patch
