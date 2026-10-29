"""Run unittest with concise failure identities in GitHub check annotations."""

import os
from pathlib import Path
import re
import sys
import unittest


class AnnotatedResult(unittest.TextTestResult):
    def _annotate(
        self,
        test,
        error,
    ) -> None:
        # Public check annotations need identities, not exception payloads or
        # subtest values. The ordinary test log retains the full traceback.
        identifier = test.id()
        fixture = re.fullmatch(
            r"(setUpClass|tearDownClass|setUpModule|tearDownModule) \(([a-zA-Z0-9_.]+)\)",
            identifier,
        )
        identifier = (
            f"{fixture[2]}.{fixture[1]}"
            if fixture is not None
            else identifier.split(" ", 1)[0]
        )
        identity = re.sub(
            r"[^a-zA-Z0-9_.]",
            "_",
            identifier,
        )[:300]
        error_type = re.sub(
            r"[^a-zA-Z0-9_]",
            "_",
            error[0].__name__,
        )[:100]
        print(
            f"::error title=Isolated regression::{identity}: {error_type}",
            flush=True,
        )

    def addError(
        self,
        test,
        err,
    ) -> None:
        super().addError(
            test,
            err,
        )
        self._annotate(
            test,
            err,
        )

    def addFailure(
        self,
        test,
        err,
    ) -> None:
        super().addFailure(
            test,
            err,
        )
        self._annotate(
            test,
            err,
        )

    def addSubTest(
        self,
        test,
        subtest,
        err,
    ) -> None:
        super().addSubTest(
            test,
            subtest,
            err,
        )
        if err is not None:
            self._annotate(
                test,
                err,
            )


if __name__ == "__main__":
    sys.path.insert(
        0,
        str(Path.cwd()),
    )
    runner = unittest.TextTestRunner(
        verbosity=0,
        resultclass=AnnotatedResult if os.environ.get("GITHUB_ACTIONS") == "true" else None,
    )
    unittest.main(
        module=None,
        testRunner=runner,
    )
