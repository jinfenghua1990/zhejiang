"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function PurchaseLegacyRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/purchase/workbench?view=orders");
  }, [router]);
  return null;
}
