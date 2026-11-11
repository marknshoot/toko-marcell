import { describe, expect, it } from "vitest";
import {
  MAX_QUERY_LENGTH,
  PAGE_SIZE,
  buildCatalogUrl,
  firstParam,
  getTotalPages,
  parseCatalogParams,
} from "../catalogParams";

describe("firstParam", () => {
  it("reads strings, arrays and missing values", () => {
    expect(firstParam("Women")).toBe("Women");
    expect(firstParam(["Women", "Men"])).toBe("Women");
    expect(firstParam([])).toBe("");
    expect(firstParam(undefined)).toBe("");
    expect(firstParam(null)).toBe("");
  });
});

describe("parseCatalogParams", () => {
  it("defaults to the unfiltered first page", () => {
    expect(parseCatalogParams({})).toEqual({ department: "All", page: 1, q: "" });
    expect(parseCatalogParams(undefined)).toEqual({
      department: "All",
      page: 1,
      q: "",
    });
  });

  it("normalises department, page and q", () => {
    expect(
      parseCatalogParams({ department: " Women ", page: "2", q: "  levis 501  " })
    ).toEqual({ department: "Women", page: 2, q: "levis 501" });
  });

  it("takes the first value when a param is repeated", () => {
    expect(
      parseCatalogParams({ department: ["Women", "Men"], page: ["3", "4"] })
    ).toEqual({ department: "Women", page: 3, q: "" });
  });

  it("falls back to page 1 for invalid pages", () => {
    for (const page of ["abc", "0", "-2", "", undefined]) {
      expect(parseCatalogParams({ page }).page).toBe(1);
    }
    expect(parseCatalogParams({ page: "2.9" }).page).toBe(2);
  });

  it("treats a whitespace query as no query and clamps long queries", () => {
    expect(parseCatalogParams({ q: "   " }).q).toBe("");
    expect(parseCatalogParams({ q: "x".repeat(150) }).q).toHaveLength(
      MAX_QUERY_LENGTH
    );
  });
});

describe("buildCatalogUrl", () => {
  it("returns the bare route for defaults", () => {
    expect(buildCatalogUrl()).toBe("/");
    expect(buildCatalogUrl({ department: "All", page: 1, q: "" })).toBe("/");
    expect(buildCatalogUrl({ q: "   " })).toBe("/");
  });

  it("keeps the parameter order stable", () => {
    expect(buildCatalogUrl({ department: "Women", page: 2, q: "levis" })).toBe(
      "/?department=Women&page=2&q=levis"
    );
  });

  it("omits page 1 and the All department", () => {
    expect(buildCatalogUrl({ department: "All", page: 1, q: "shoes" })).toBe(
      "/?q=shoes"
    );
    expect(buildCatalogUrl({ department: "Men", page: 1 })).toBe(
      "/?department=Men"
    );
  });

  it("encodes spaces and special characters", () => {
    expect(buildCatalogUrl({ q: "levis 501" })).toBe("/?q=levis+501");
    expect(buildCatalogUrl({ q: "a&b" })).toBe("/?q=a%26b");
  });
});

describe("getTotalPages", () => {
  it("rounds up and never drops below one page", () => {
    expect(getTotalPages(0)).toBe(1);
    expect(getTotalPages(PAGE_SIZE)).toBe(1);
    expect(getTotalPages(PAGE_SIZE + 1)).toBe(2);
    expect(getTotalPages(4670)).toBe(195);
    expect(getTotalPages("abc")).toBe(1);
  });
});
