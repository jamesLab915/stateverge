import countries from "@/data/countries.json";
import scores from "@/data/scores.json";

export type Country = {
  code: string;
  name: string;
  region: string;
  capital: string;
  population: number;
  gdp: number;
  summary: string;
};

export type Score = {
  code: string;
  governance: number;
  social_order: number;
  economy: number;
  human_capital: number;
  infrastructure: number;
  innovation: number;
  openness: number;
  future_potential: number;
  overall: number;
  risk: number;
  opportunity: number;
};

export function getCountries(): Country[] {
  return countries as Country[];
}

export function getScores(): Score[] {
  return scores as Score[];
}

export function getCountryCards() {
  const countryList = getCountries();
  const scoreList = getScores();

  return countryList.map((country) => {
    const score = scoreList.find((s) => s.code === country.code);
    return {
      ...country,
      score,
    };
  });
}
