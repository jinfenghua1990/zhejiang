"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function TaxInvoicesRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/finance/invoices");
  }, [router]);
  return null;
}
