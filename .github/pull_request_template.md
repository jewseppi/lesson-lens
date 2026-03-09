## Summary

<!-- Describe what this PR does -->

## Preview

Check the Cloudflare Pages status check "Details" link for the preview deployment.

> Note: Preview is frontend-only. API calls require a running backend.

## Checklist

- [ ] Backend tests pass (`cd api && pytest tests/`)
- [ ] Frontend tests pass (`cd web && npm test`)
- [ ] TypeScript compiles (`cd web && npx tsc --noEmit`)
- [ ] Visual review completed via Cloudflare preview URL
