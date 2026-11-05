#!/usr/bin/env bash
# Create the "Ticket Rush plan" GitHub Projects board and add every plan issue from both repos.
#
# Run from your own machine (not from a Claude session: Projects are organization-level and
# need GitHub's GraphQL API). Safe to re-run: it reuses the board and skips items already on it.
#
#   gh auth refresh -s project          # once: grants the CLI the "project" scope
#   scripts/github/create_project_board.sh
#
# Works with the bash that ships with macOS (3.2).
set -euo pipefail

OWNER=FluentSolutionsLab
TITLE="Ticket Rush plan"
REPOS="FluentSolutionsLab/ticket-rush FluentSolutionsLab/distsys-labs"

number=$(gh project list --owner "$OWNER" --format json --limit 100 \
  --jq ".projects[] | select(.title == \"$TITLE\") | .number" | head -1)

if [ -z "$number" ]; then
  number=$(gh project create --owner "$OWNER" --title "$TITLE" --format json --jq .number)
  echo "Created project #$number"
else
  echo "Using existing project #$number"
fi

for repo in $REPOS; do
  gh project link "$number" --owner "$OWNER" --repo "$repo" >/dev/null 2>&1 || true
done

# A "Block" single-select field makes filtering and grouping by block easy.
if ! gh project field-list "$number" --owner "$OWNER" --format json --jq '.fields[].name' | grep -qx "Block"; then
  gh project field-create "$number" --owner "$OWNER" --name "Block" \
    --data-type SINGLE_SELECT --single-select-options "Block 1,Block 2,Block 3,Block 4" >/dev/null
  echo "Added field: Block"
fi

project_id=$(gh project view "$number" --owner "$OWNER" --format json --jq .id)
block_field=$(gh project field-list "$number" --owner "$OWNER" --format json \
  --jq '.fields[] | select(.name == "Block") | .id')

block_option_id() {
  gh project field-list "$number" --owner "$OWNER" --format json \
    --jq ".fields[] | select(.name == \"Block\") | .options[] | select(.name == \"Block $1\") | .id"
}
opt1=$(block_option_id 1); opt2=$(block_option_id 2); opt3=$(block_option_id 3); opt4=$(block_option_id 4)

existing=$(gh project item-list "$number" --owner "$OWNER" --format json --limit 1000 \
  --jq '.items[].content.url')

for repo in $REPOS; do
  gh issue list -R "$repo" --state all --limit 500 \
    --json url,labels --jq '.[] | "\(.url) \([.labels[].name | select(startswith("block-"))] | first // "")"' |
  while read -r url block_label; do
    if printf '%s\n' "$existing" | grep -qx "$url"; then continue; fi
    item_id=$(gh project item-add "$number" --owner "$OWNER" --url "$url" --format json --jq .id)
    case "$block_label" in
      block-1) option_id=$opt1 ;; block-2) option_id=$opt2 ;;
      block-3) option_id=$opt3 ;; block-4) option_id=$opt4 ;; *) option_id= ;;
    esac
    if [ -n "$option_id" ]; then
      gh project item-edit --id "$item_id" --project-id "$project_id" \
        --field-id "$block_field" --single-select-option-id "$option_id" >/dev/null
    fi
    echo "added $url"
  done
done

url=$(gh project view "$number" --owner "$OWNER" --format json --jq .url)
cat <<MSG

Board ready: $url

Finish in the browser (views and workflows can't be created from the CLI):
  1. Board view: Status columns, filter by the current week's milestone.
  2. Roadmap view: dates from Milestone, zoom to Quarter, to see all 26 weeks on one timeline.
  3. Workflows: turn on "Auto-add to project" for both repos so new issues land on the board.
MSG
