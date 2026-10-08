from verifier.cli import main

# Guarded so that a `spawn`-started worker process (task publication builds) importing this module
# as `__mp_main__` does not run the command line a second time.
if __name__ == "__main__":
    raise SystemExit(main())
