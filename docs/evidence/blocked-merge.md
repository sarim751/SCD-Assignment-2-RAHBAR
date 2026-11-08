# Evidence: Blocked Merge Gate on Pull Request

This evidence captures the GitHub Branch Protection and CI gate actively blocking a merge to `main`.

## Scenario
1. A Pull Request (#1) was opened targeting `main` containing an uncalibrated chunking configuration that caused `recall@5` to drop below the committed baseline in `eval/baseline.json`.
2. The `ci / e2e-eval` check failed with:
   `recall@5 dropped 8.20% below baseline 93.75% (max allowed: 5.00%)`
3. Status: **Required status checks have not passed**
4. The GitHub UI rendered the green "Merge pull request" button **disabled and greyed out** under branch protection rule `main`.
5. After reverting the faulty chunker parameters and ensuring recall@5 met the threshold, `e2e-eval` transitioned to **All checks have passed (green)**, unlocking the merge button.
