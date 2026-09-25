from nodyra_runtime.server import main

if __name__ == "__main__":
    # The guard is load-bearing on Windows: ``multiprocessing`` spawn workers
    # re-import the parent's ``__main__`` module, and an unguarded ``main()``
    # would enter the server loop inside every code-node isolator worker —
    # blocking the spawn bootstrap (code nodes time out) and racing the real
    # worker's stdin reader for the pool pipe.
    main()
