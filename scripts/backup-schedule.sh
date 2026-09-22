#!/usr/bin/env bash
# macOS launchd：安装/卸载本项目的自动备份与恢复演练计划。
# 不会在代码拉取后自动启用；只有显式执行 install 才写入 ~/Library/LaunchAgents。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ACTION="${1:-status}"
UID_NOW="$(id -u)"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
BACKUP_LABEL="com.gino.ecommerce-dashboard.backup"
RESTORE_LABEL="com.gino.ecommerce-dashboard.restore-check"
R2_LABEL="com.gino.ecommerce-dashboard.backup-r2"
KODO_LABEL="com.gino.ecommerce-dashboard.cold-backup-kodo"
WEBDAV_LABEL="com.gino.ecommerce-dashboard.backup-webdav"
BACKUP_PLIST="$LAUNCH_DIR/$BACKUP_LABEL.plist"
RESTORE_PLIST="$LAUNCH_DIR/$RESTORE_LABEL.plist"
R2_PLIST="$LAUNCH_DIR/$R2_LABEL.plist"
KODO_PLIST="$LAUNCH_DIR/$KODO_LABEL.plist"
WEBDAV_PLIST="$LAUNCH_DIR/$WEBDAV_LABEL.plist"
SCHEDULE_STAMP="$LAUNCH_DIR/.ecommerce-dashboard.backup-schedule.sha256"

# 可通过环境变量覆盖，但默认避开白天业务时间。
BACKUP_HOUR="${BACKUP_HOUR:-2}"
BACKUP_MINUTE="${BACKUP_MINUTE:-30}"
RESTORE_WEEKDAY="${RESTORE_WEEKDAY:-0}"   # launchd: 0 = Sunday
RESTORE_HOUR="${RESTORE_HOUR:-3}"
RESTORE_MINUTE="${RESTORE_MINUTE:-30}"
R2_HOUR="${R2_HOUR:-3}"
R2_MINUTE="${R2_MINUTE:-0}"
KODO_HOUR="${KODO_HOUR:-4}"
KODO_MINUTE="${KODO_MINUTE:-0}"
WEBDAV_HOUR="${WEBDAV_HOUR:-5}"
WEBDAV_MINUTE="${WEBDAV_MINUTE:-0}"
PATH_VALUE="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

write_backup_plist() {
  cat > "$BACKUP_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$BACKUP_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string>
    <string>cd '$ROOT' &amp;&amp; make backup</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>$BACKUP_HOUR</integer>
    <key>Minute</key><integer>$BACKUP_MINUTE</integer>
  </dict>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$PATH_VALUE</string>
  </dict>
  <key>StandardOutPath</key><string>/tmp/ecom_backup.log</string>
  <key>StandardErrorPath</key><string>/tmp/ecom_backup.err</string>
</dict></plist>
EOF
}

write_r2_plist() {
  cat > "$R2_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$R2_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string>
    <string>cd '$ROOT' &amp;&amp; make backup-r2</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>$R2_HOUR</integer>
    <key>Minute</key><integer>$R2_MINUTE</integer>
  </dict>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$PATH_VALUE</string>
  </dict>
  <key>StandardOutPath</key><string>/tmp/ecom_r2_backup.log</string>
  <key>StandardErrorPath</key><string>/tmp/ecom_r2_backup.err</string>
</dict></plist>
EOF
}

write_kodo_plist() {
  cat > "$KODO_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$KODO_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string>
    <string>cd '$ROOT' &amp;&amp; make cold-backup-kodo</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>$KODO_HOUR</integer>
    <key>Minute</key><integer>$KODO_MINUTE</integer>
  </dict>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$PATH_VALUE</string>
  </dict>
  <key>StandardOutPath</key><string>/tmp/ecom_kodo_cold_backup.log</string>
  <key>StandardErrorPath</key><string>/tmp/ecom_kodo_cold_backup.err</string>
</dict></plist>
EOF
}

write_webdav_plist() {
  cat > "$WEBDAV_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$WEBDAV_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string>
    <string>cd '$ROOT' &amp;&amp; make backup-webdav</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>$WEBDAV_HOUR</integer>
    <key>Minute</key><integer>$WEBDAV_MINUTE</integer>
  </dict>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$PATH_VALUE</string>
  </dict>
  <key>StandardOutPath</key><string>/tmp/ecom_webdav_backup.log</string>
  <key>StandardErrorPath</key><string>/tmp/ecom_webdav_backup.err</string>
</dict></plist>
EOF
}

write_restore_plist() {
  cat > "$RESTORE_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$RESTORE_LABEL</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>-lc</string>
    <string>cd '$ROOT' &amp;&amp; make restore-check</string>
  </array>
  <key>StartCalendarInterval</key><dict>
    <key>Weekday</key><integer>$RESTORE_WEEKDAY</integer>
    <key>Hour</key><integer>$RESTORE_HOUR</integer>
    <key>Minute</key><integer>$RESTORE_MINUTE</integer>
  </dict>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$PATH_VALUE</string>
  </dict>
  <key>StandardOutPath</key><string>/tmp/ecom_restore_check.log</string>
  <key>StandardErrorPath</key><string>/tmp/ecom_restore_check.err</string>
</dict></plist>
EOF
}

bootout_if_loaded() {
  local label="$1"
  launchctl bootout "gui/$UID_NOW/$label" >/dev/null 2>&1 || true
}

schedule_signature() {
  {
    printf '%s\n' "$ROOT"
    cat "$0"
  } | shasum -a 256 | awk '{print $1}'
}

all_schedules_loaded() {
  local label
  for label in "$BACKUP_LABEL" "$RESTORE_LABEL" "$R2_LABEL" "$KODO_LABEL" "$WEBDAV_LABEL"; do
    launchctl print "gui/$UID_NOW/$label" >/dev/null 2>&1 || return 1
  done
  return 0
}

case "$ACTION" in
  install)
    mkdir -p "$LAUNCH_DIR"
    bootout_if_loaded "$BACKUP_LABEL"
    bootout_if_loaded "$RESTORE_LABEL"
    bootout_if_loaded "$R2_LABEL"
    bootout_if_loaded "$KODO_LABEL"
    bootout_if_loaded "$WEBDAV_LABEL"
    write_backup_plist
    write_restore_plist
    write_r2_plist
    write_kodo_plist
    write_webdav_plist
    plutil -lint "$BACKUP_PLIST" >/dev/null
    plutil -lint "$RESTORE_PLIST" >/dev/null
    plutil -lint "$R2_PLIST" >/dev/null
    plutil -lint "$KODO_PLIST" >/dev/null
    plutil -lint "$WEBDAV_PLIST" >/dev/null
    launchctl bootstrap "gui/$UID_NOW" "$BACKUP_PLIST"
    launchctl bootstrap "gui/$UID_NOW" "$RESTORE_PLIST"
    launchctl bootstrap "gui/$UID_NOW" "$R2_PLIST"
    launchctl bootstrap "gui/$UID_NOW" "$KODO_PLIST"
    launchctl bootstrap "gui/$UID_NOW" "$WEBDAV_PLIST"
    schedule_signature > "$SCHEDULE_STAMP"
    echo "已安装本地基础备份：每天 $(printf '%02d:%02d' "$BACKUP_HOUR" "$BACKUP_MINUTE")"
    echo "已安装 R2 主备份：每天 $(printf '%02d:%02d' "$R2_HOUR" "$R2_MINUTE")（每日模块化，默认每 10 天全量）"
    echo "已安装 Kodo 每日全量冷备上传：每天 $(printf '%02d:%02d' "$KODO_HOUR" "$KODO_MINUTE")（KODO_COLD_ENABLED=0 时仅跳过，不上传）"
    echo "已安装坚果云 WebDAV 每日全量备份：每天 $(printf '%02d:%02d' "$WEBDAV_HOUR" "$WEBDAV_MINUTE")（未配置/停用时仅跳过）"
    echo "已安装恢复演练：每周日 $(printf '%02d:%02d' "$RESTORE_HOUR" "$RESTORE_MINUTE")"
    echo "日志：/tmp/ecom_backup.log /tmp/ecom_r2_backup.log /tmp/ecom_kodo_cold_backup.log /tmp/ecom_webdav_backup.log /tmp/ecom_restore_check.log"
    ;;
  uninstall)
    bootout_if_loaded "$BACKUP_LABEL"
    bootout_if_loaded "$RESTORE_LABEL"
    bootout_if_loaded "$R2_LABEL"
    bootout_if_loaded "$KODO_LABEL"
    bootout_if_loaded "$WEBDAV_LABEL"
    rm -f "$BACKUP_PLIST" "$RESTORE_PLIST" "$R2_PLIST" "$KODO_PLIST" "$WEBDAV_PLIST" "$SCHEDULE_STAMP"
    echo "已卸载本地基础备份、R2 主备份、Kodo 冷备、WebDAV 备份与恢复演练计划。"
    ;;
  ensure)
    mkdir -p "$LAUNCH_DIR"
    wanted_signature="$(schedule_signature)"
    current_signature="$(cat "$SCHEDULE_STAMP" 2>/dev/null || true)"
    if [[ "$current_signature" != "$wanted_signature" ]] || ! all_schedules_loaded; then
      echo "备份计划缺失或已变更，自动刷新 launchd 配置。"
      "$0" install
    else
      echo "备份计划已安装且为当前版本，无需刷新。"
    fi
    ;;
  status)
    echo "==> $BACKUP_LABEL"
    launchctl print "gui/$UID_NOW/$BACKUP_LABEL" 2>/dev/null | sed -n '1,25p' || echo "未安装/未加载"
    echo "==> $RESTORE_LABEL"
    launchctl print "gui/$UID_NOW/$RESTORE_LABEL" 2>/dev/null | sed -n '1,25p' || echo "未安装/未加载"
    echo "==> $R2_LABEL"
    launchctl print "gui/$UID_NOW/$R2_LABEL" 2>/dev/null | sed -n '1,25p' || echo "未安装/未加载"
    echo "==> $KODO_LABEL"
    launchctl print "gui/$UID_NOW/$KODO_LABEL" 2>/dev/null | sed -n '1,25p' || echo "未安装/未加载"
    echo "==> $WEBDAV_LABEL"
    launchctl print "gui/$UID_NOW/$WEBDAV_LABEL" 2>/dev/null | sed -n '1,25p' || echo "未安装/未加载"
    ;;
  *)
    echo "用法：$0 {install|ensure|uninstall|status}"
    exit 2
    ;;
esac
