# wizarr-overrides — historical in-image overrides of Wizarr media clients

These files were bind-mounted over `app/services/media/{jellyfin,romm}.py`
inside the Wizarr container image, replacing the stock clients. **Both are
retired** (2026-10-14) and preserved here for reference.

They are derivative of [Wizarr](https://github.com/wizarrrr/wizarr) (MIT,
© Wizarr contributors): each is a snapshot of the upstream module of its day
with household patches applied. No authorship metadata has been added; see the
repository LICENSE.

## `romm.py` (2026-08-30)

A full Wizarr media client for [RomM](https://github.com/rommapp/romm), written
before upstream Wizarr shipped native RomM support. Registered via
`@register_media_client("romm")`; implements invite→user creation against
RomM's API with library (platform) scoping. **Superseded:** current upstream
Wizarr ships its own RomM client — use that instead.

## `jellyfin.py` (2026-08-29)

Patched copy of the upstream Jellyfin client of that date. **Why retired:**
Wizarr's image auto-updated and new upstream code imported
`POSTER_IMAGE_PARAMS` from this module's path; the stale override shadowed the
new module and crash-looped the container (ImportError). The override mounts
were removed and the stock client restored.

**Lesson worth publishing:** bind-mount overrides of application internals are
version-pinned to the image of the day they were made. Pair any such override
with a pinned image tag, or it will eventually shadow a module the application
expects to have evolved.
