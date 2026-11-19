"""CLI: AN-37739 step 1 - coverage summary across every tenant with vehicle_driver_history
data. Read-only. Requires frs_src importable and DB credentials configured. Not run yet.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from frs_src.config import cfg

from an37739.coverage import coverage_summary, enabled_tenant_ids


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()

    session = cfg.SQLALCHEMY.session()
    try:
        tenant_ids = enabled_tenant_ids(session)
        print(f"{len(tenant_ids)} tenant(s) with vehicle_driver_history data: {tenant_ids}")
        print()
        print(coverage_summary(session).to_string(index=False))
    finally:
        session.close()


if __name__ == "__main__":
    main()
