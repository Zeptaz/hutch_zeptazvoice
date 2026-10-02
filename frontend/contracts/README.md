# Resolve contract copy

`openapi.json` and `examples.json` are copied from `docs/contracts/` on the `ResolveDev` branch of
[hutch_resolve](https://github.com/Zeptaz/hutch_resolve). Resolve owns them; do not edit here.

Refresh, then regenerate the frontend types:

```sh
curl -fsSL https://raw.githubusercontent.com/Zeptaz/hutch_resolve/ResolveDev/docs/contracts/openapi.json -o contracts/openapi.json
curl -fsSL https://raw.githubusercontent.com/Zeptaz/hutch_resolve/ResolveDev/docs/contracts/examples.json -o contracts/examples.json
npm run gen:api
```
