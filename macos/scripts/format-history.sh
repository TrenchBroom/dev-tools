#!/bin/bash

dir=$(dirname "$0")
folder=$1

"$dir/format-directory.sh" "$folder"

if ! git diff --quiet -- "$folder"; then
  git add -- "$folder"
  git commit --fixup=HEAD
fi
