import { redirect } from "next/navigation";

export default function LegacyWarehouseSettingsPage() {
  redirect("/supply-chain/warehouses");
}
