"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function Alibaba1688ImportRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/data-center-import?tab=alibaba1688");
  }, [router]);
  return null;
}
