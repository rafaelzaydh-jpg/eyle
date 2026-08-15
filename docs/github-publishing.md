# Publishing

A publishable ECC artifact must be produced from a clean tree after tests. Remove `__pycache__`, `.pytest_cache`, bytecode and mutable Runtime state before packaging.

Run the fail-closed release verifier on the source tree, create the ZIP, extract it to a fresh directory, then run the verifier again on the extracted artifact. Publish the SHA-256 with the release.
