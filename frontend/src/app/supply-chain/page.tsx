import { redirect } from "next/navigation";

export default function LegacySupplyChainPage() {
  redirect("/purchase/workbench?view=orders");
}
