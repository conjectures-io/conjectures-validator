"""Private subprocess entry point; limits are installed before importing the archive parser."""
import json
import os
import resource
import sys


def main():
    descriptor, cpu_seconds, memory_bytes, index_bytes = map(int, sys.argv[1:])
    for kind, value in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_AS, memory_bytes),
                        (resource.RLIMIT_CPU, cpu_seconds), (resource.RLIMIT_FSIZE, index_bytes),
                        (resource.RLIMIT_NOFILE, 64)):
        _, hard = resource.getrlimit(kind)
        value = value if hard == resource.RLIM_INFINITY else min(value, hard)
        resource.setrlimit(kind, (value, value))
    os.nice(10)
    from submission_api.trajectory_storage import FilePackage
    from submission_api.trajectory_schema import Manifest, PackageError, parse_package
    from submission_api.trajectory_intake import package_errors
    from submission_api.trajectory_validation import MAX_REPLY_BYTES, RESOURCE_MESSAGE

    package = FilePackage.__new__(FilePackage)
    package.file = os.fdopen(descriptor, "rb")
    package.size = os.fstat(descriptor).st_size
    try:
        summary = parse_package(package)
        result = {"manifest": summary.manifest.model_dump(mode="json", include=set(Manifest.model_fields)),
                  "fingerprint": summary.fingerprint.hex(), "proof_first_seen": summary.proof_first_seen.isoformat(),
                  "events": summary.events, "artifacts": summary.artifacts, "digest": package.digest().hex()}
    except PackageError as error:
        result = {"error": package_errors(error)}
    except Exception:
        result = {"error": [{"path": [], "type": "value_error", "message": RESOURCE_MESSAGE}]}
    finally:
        package.close()
    reply = json.dumps(result)
    if len(reply.encode()) + 1 > MAX_REPLY_BYTES:
        sys.exit(1)
    print(reply, flush=True)


if __name__ == "__main__":
    main()
