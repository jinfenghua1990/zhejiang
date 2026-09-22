"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function JackyunImportRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/data-center-import?tab=jackyun");
  }, [router]);
  return null;
}
