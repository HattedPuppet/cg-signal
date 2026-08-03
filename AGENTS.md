# Repository workflow

For low-risk changes to this hobby/vibe-coded application:

1. After opening or updating a pull request, spawn an independent reviewer agent to inspect the complete PR diff and run the relevant tests.
2. The reviewer must report findings by severity and must not edit the reviewed branch.
3. Check the pull request's required checks after the review.
4. If the reviewer reports no material findings, relevant tests pass, and required checks are successful, merge the pull request without waiting for another confirmation. After the merge succeeds, delete the merged remote branch.
5. If any material finding or failing/pending required check remains, do not merge. Leave the pull request open and report the blocker.

Do not auto-merge changes involving credentials, authentication or authorization, billing, destructive data operations, production database migrations, or other high-impact external actions unless the user explicitly authorizes that specific merge.
