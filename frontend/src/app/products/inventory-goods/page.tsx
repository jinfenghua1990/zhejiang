"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function InventoryGoodsLegacyRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/inventory?tab=goods");
  }, [router]);
  return null;
}
