import { getCategories } from "@/lib/api";
import { buildCatalogUrl } from "@/lib/catalogParams";
import FilterLink from "./FilterLink";

// Categories stream independently of the products: both Suspense boundaries in
// app/page.js start their fetch at the same time.
export default async function CatalogFilters({ department, q }) {
  let departments = [];

  try {
    const data = await getCategories();
    departments = Array.isArray(data?.departments) ? data.departments : [];
  } catch {
    // A failed facet fetch must not hide the catalog; "All" still works.
    departments = [];
  }

  const options = [{ name: "All", count: null }, ...departments];

  return (
    <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="Departments">
      {options.map((dept) => (
        <FilterLink
          key={dept.name}
          href={buildCatalogUrl({ department: dept.name, page: 1, q })}
          active={dept.name === department}
        >
          {dept.name}
          {dept.count !== null ? ` (${dept.count})` : ""}
        </FilterLink>
      ))}
    </div>
  );
}
