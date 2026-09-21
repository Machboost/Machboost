# Starting MachBoost remotely

`machboost start` starts or reuses a local resident server. On a desktop it first
tries the matching installed Mac app. Over SSH it skips opening the app.

```sh
machboost start --headless
machboost warm MODEL
machboost ps
machboost shutdown
```

Headless startup uses the matching app's bundled Python runtime when available,
so a minimal CLI installation does not need a second MLX installation. The
server is detached from the SSH session. No model is downloaded by `start`.

For a foreground process, including under a service supervisor, use
`machboost serve`. Startup defaults to loopback. LAN serving requires explicit
host configuration and authentication; `start` does not enable network sharing.

This is not a boot service installer: after a reboot, start the server again
over SSH or configure a separately managed macOS service. A login item starts
after login, not before login. Neither command bypasses FileVault's initial
disk unlock or enables SSH. Opening a GUI app is not required for inference.

The app and CLI share model caches and can connect to the same endpoint, but
headless startup does not import all desktop sharing settings or credentials.
