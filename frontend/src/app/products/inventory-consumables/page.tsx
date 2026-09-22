"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function InventoryConsumablesLegacyRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/inventory?tab=consumables");
  }, [router]);
  return null;
}
