# Stage 5 decisions

R1 (2026-11-11, Task 6) Authentication goes through `ttalkkak ops serve` as an
Envoy Gateway `extAuth` service instead of SecurityPolicy `jwt` + `apiKeyAuth`.
`apiKeyAuth` needs raw keys in a Secret (we keep only hashes) and the two
methods in one SecurityPolicy must both pass. `/authz` accepts either a
`tk_` key (hash lookup) or a JWT (JWKS signature, issuer, expiry) and returns
`x-ttalkkak-tenant`, `x-ttalkkak-subject`, `x-ttalkkak-cache-salt`.

R2 (Task 6) cache_salt: AI Gateway `bodyMutation` is ignored for InferencePool
backends (api/v1beta1/ai_gateway_route.go, v1.2.1). An EnvoyExtensionPolicy Lua
filter appends `"cache_salt"` as the last key of the JSON body from the extAuth
header (vLLM's JSON parser keeps the last duplicate key, so a client-sent salt
is overridden). Filter order relative to the AI Gateway ext_proc is not
verified yet: Task 12. Fallback: ops serve as a body-rewriting proxy.

R3 (Task 6) Gateway API Inference Extension pinned to v1.5.0: the v1.6.2 EPP
image is not on registry.k8s.io.

R4 (Task 6) The gateway listener is HTTPS (Secret `ttalkkak-tls`) because the
CLI refuses plain-http gateways and issuers; Dex is served at
`https://<hostname>/dex`.
