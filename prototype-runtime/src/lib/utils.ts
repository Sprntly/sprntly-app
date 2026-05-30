import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// Standard shadcn/ui class-merge helper. Vendored shadcn components import
// `cn` from "@/lib/utils". The generated prototype may overlay its own
// (simpler) utils.ts; both expose a variadic `cn(...) => string`, so the
// vendored components build either way.
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
