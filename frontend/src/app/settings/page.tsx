"use client";

import { useEffect } from "react";
import { syncWorkspaceUrl } from "@/lib/workspace/url-sync";

export default function SettingsPage() {
  useEffect(() => {
    syncWorkspaceUrl("/settings/backup", "replace");
  }, []);
  return null;
}
