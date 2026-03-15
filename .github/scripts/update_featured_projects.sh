#!/usr/bin/env bash
set -euo pipefail

OWNER="${GITHUB_REPOSITORY_OWNER:-ccstudentcc}"
README_PATH="README.md"
START_MARKER="<!--START_SECTION:featured-->"
END_MARKER="<!--END_SECTION:featured-->"
API_URL="https://api.github.com/users/${OWNER}/repos?sort=updated&per_page=100"

if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  AUTH_HEADER=("-H" "Authorization: Bearer ${GITHUB_TOKEN}")
else
  AUTH_HEADER=()
fi

repos_json="$(curl -sS "${AUTH_HEADER[@]}" -H "Accept: application/vnd.github+json" "${API_URL}")"

# Select the 3 most recently updated public, non-fork, non-archived repos, excluding the profile repo.
mapfile -t repos < <(
  echo "${repos_json}" | jq -r --arg owner "${OWNER}" '
    map(select(.fork == false and .archived == false and .name != $owner))
    | sort_by(.pushed_at)
    | reverse
    | .[:3]
    | .[].name
  '
)

if [[ ${#repos[@]} -eq 0 ]]; then
  echo "No repositories available to feature."
  exit 0
fi

block_file="$(mktemp)"
{
  echo '<div align="center">'
  echo

  for repo in "${repos[@]}"; do
    echo "<a href=\"https://github.com/${OWNER}/${repo}\">"
    echo '  <picture>'
    echo "    <source media=\"(prefers-color-scheme: dark)\" srcset=\"https://github-readme-stats.vercel.app/api/pin/?username=${OWNER}&repo=${repo}&theme=tokyonight&hide_border=true\" />"
    echo "    <img src=\"https://github-readme-stats.vercel.app/api/pin/?username=${OWNER}&repo=${repo}&theme=default&hide_border=true\" alt=\"${repo}\" />"
    echo '  </picture>'
    echo '</a>'
  done

  echo
  echo '</div>'
} > "${block_file}"

python .github/scripts/readme_utils.py "${README_PATH}" "${START_MARKER}" "${END_MARKER}" --block-file "${block_file}" --allow-missing-markers
rm -f "${block_file}"

echo "Updated featured projects: ${repos[*]}"
