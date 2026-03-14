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

temp_readme="$(mktemp)"
awk -v start="${START_MARKER}" -v end="${END_MARKER}" -v block_file="${block_file}" '
  BEGIN {
    in_block = 0
    while ((getline line < block_file) > 0) {
      block = block line "\n"
    }
    close(block_file)
  }
  {
    if ($0 == start) {
      print $0
      printf "%s", block
      in_block = 1
      next
    }

    if ($0 == end) {
      in_block = 0
      print $0
      next
    }

    if (!in_block) {
      print $0
    }
  }
' "${README_PATH}" > "${temp_readme}"

mv "${temp_readme}" "${README_PATH}"
rm -f "${block_file}"

echo "Updated featured projects: ${repos[*]}"
