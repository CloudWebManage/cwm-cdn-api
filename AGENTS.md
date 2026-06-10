# Agent Instructions

## Project Conventions

- Keep generated nginx configuration in `tenant-nginx/render_nginx_conf.py` focused on rendering and OpenResty integration.
- Put reusable pure Lua logic under `lua/` as modules.
- Add or update Busted specs under `lua/` for Lua module behavior.
- Run `just lua-tests` after changing Lua modules or generated nginx Lua blocks.
- Run `uv run pytest tests/test_tenant_nginx.py -q` after changing tenant nginx rendering, packaging, or tests.

## Tenant Nginx Lua Modules

- OpenResty runtime modules from `lua/` must be copied into `/usr/local/openresty/lualib/` by `tenant-nginx/Dockerfile`.
- The tenant nginx Docker build context is the repository root so the Dockerfile can copy both `tenant-nginx/` and `lua/` files.
- Keep Lua `require` calls inside OpenResty Lua block contexts unless the target file is explicitly valid nginx configuration syntax.

## Validation Notes

- `just lua-tests` uses `busted -m 'lua/?.lua' lua/`.
- E2E tenant nginx tests are skipped unless `E2E=yes` is set.
- If Docker CLI exists but the daemon is unavailable, report that image-build validation could not be completed instead of claiming it passed.
