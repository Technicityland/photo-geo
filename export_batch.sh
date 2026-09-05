#!/bin/zsh
cd "$(dirname "$0")"
while read U; do
  [ -n "$(ls export_A/$U 2>/dev/null)" ] && continue
  mkdir -p export_A/$U
  osascript -e "tell application \"Photos\"
set mi to media item id \"$U/L0/001\"
with timeout of 300 seconds
export {mi} to POSIX file \"$PWD/export_A/$U\" with using originals
end timeout
end tell" >> export_batch.log 2>&1 || echo "FAIL $U" >> export_batch.log
done < exportA_fix.txt
echo ALLDONE >> export_batch.log
