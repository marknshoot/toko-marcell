import { render, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SizeAdvisor from "../SizeAdvisor";
import { postSizingRecommend } from "../../lib/api";

vi.mock("../../lib/api", () => ({
  postSizingRecommend: vi.fn(),
}));

function advice(overrides = {}) {
  return {
    asin: "B01",
    applicable: true,
    size_kind: "tops",
    recommendation: "Mimin sarankan coba ukuran M dulu kak.",
    suggested: { size: "M" },
    source: "store_estimate",
    confidence: "medium",
    required_inputs: [],
    disclaimer: "Ini panduan umum, bukan patokan pasti.",
    reason: null,
    ...overrides,
  };
}

describe("SizeAdvisor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    postSizingRecommend.mockResolvedValue(advice());
  });

  it("asks for height and weight for tops and shows source/confidence/disclaimer", async () => {
    const info = {
      asin: "B01",
      size_kind: "tops",
      applicable: true,
      required_inputs: ["height_cm", "weight_kg"],
      reason: null,
    };
    const { getByLabelText, getByText } = render(<SizeAdvisor asin="B01" info={info} />);

    fireEvent.change(getByLabelText("Tinggi badan (cm)"), { target: { value: "170" } });
    fireEvent.change(getByLabelText("Berat badan (kg)"), { target: { value: "65" } });
    fireEvent.click(getByText("Lihat saran ukuran"));

    await waitFor(() =>
      expect(postSizingRecommend).toHaveBeenCalledWith({
        asin: "B01",
        heightCm: 170,
        weightKg: 65,
      })
    );
    expect(await getByText("Mimin sarankan coba ukuran M dulu kak.")).toBeTruthy();
    expect(getByText("Sumber: Estimasi toko")).toBeTruthy();
    expect(getByText("Keyakinan sedang")).toBeTruthy();
    expect(getByText("Ini panduan umum, bukan patokan pasti.")).toBeTruthy();
  });

  it("asks for foot length for footwear", async () => {
    const info = {
      asin: "B02",
      size_kind: "footwear",
      applicable: true,
      required_inputs: ["foot_length_cm"],
      reason: null,
    };
    const { getByLabelText, getByText } = render(<SizeAdvisor asin="B02" info={info} />);

    fireEvent.change(getByLabelText("Panjang kaki (cm)"), { target: { value: "26.5" } });
    fireEvent.click(getByText("Lihat saran ukuran"));

    await waitFor(() =>
      expect(postSizingRecommend).toHaveBeenCalledWith({ asin: "B02", footLengthCm: 26.5 })
    );
  });

  it("asks for waist for belts", async () => {
    const info = {
      asin: "B03",
      size_kind: "belt",
      applicable: true,
      required_inputs: ["waist_cm"],
      reason: null,
    };
    const { getByLabelText, getByText } = render(<SizeAdvisor asin="B03" info={info} />);

    fireEvent.change(getByLabelText("Lingkar pinggang (cm)"), { target: { value: "82" } });
    fireEvent.click(getByText("Lihat saran ukuran"));

    await waitFor(() =>
      expect(postSizingRecommend).toHaveBeenCalledWith({ asin: "B03", waistCm: 82 })
    );
  });

  it("asks for head circumference for hats", async () => {
    const info = {
      asin: "B04",
      size_kind: "headwear",
      applicable: true,
      required_inputs: ["head_circumference_cm"],
      reason: null,
    };
    const { getByLabelText, getByText } = render(<SizeAdvisor asin="B04" info={info} />);

    fireEvent.change(getByLabelText("Lingkar kepala (cm)"), { target: { value: "57" } });
    fireEvent.click(getByText("Lihat saran ukuran"));

    await waitFor(() =>
      expect(postSizingRecommend).toHaveBeenCalledWith({ asin: "B04", headCircumferenceCm: 57 })
    );
  });

  it("shows a dimensions note instead of a size form", () => {
    const info = {
      asin: "B05",
      size_kind: "dimensions",
      applicable: false,
      required_inputs: [],
      reason: "Baca dimensi dan kapasitas produk.",
    };
    const { queryByRole, getByText } = render(<SizeAdvisor asin="B05" info={info} />);

    expect(queryByRole("button", { name: "Lihat saran ukuran" })).toBeNull();
    expect(getByText(/Baca dimensi dan kapasitas produk/)).toBeTruthy();
  });

  it("shows an unsupported note instead of guessing S/M/L", () => {
    const info = {
      asin: "B06",
      size_kind: "unsupported",
      applicable: false,
      required_inputs: [],
      reason: "Lingerie butuh ukuran tubuh yang belum kami modelkan.",
    };
    const { queryByRole, getByText } = render(<SizeAdvisor asin="B06" info={info} />);

    expect(queryByRole("button", { name: "Lihat saran ukuran" })).toBeNull();
    expect(getByText(/Lingerie butuh ukuran tubuh/)).toBeTruthy();
  });

  it("shows the one-size note without inputs", () => {
    const info = {
      asin: "B07",
      size_kind: "one_size",
      applicable: true,
      required_inputs: [],
      reason: "Produk ini ukuran tunggal (one size).",
    };
    const { queryByRole, getByText } = render(<SizeAdvisor asin="B07" info={info} />);

    expect(queryByRole("button", { name: "Lihat saran ukuran" })).toBeNull();
    expect(getByText(/ukuran tunggal \(one size\)/)).toBeTruthy();
  });
});
