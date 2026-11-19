import type { ActivityCategory, ActivityIn, Destination } from "../../client/types.gen";
import { addDays } from "./tripRules";
import type { TripFields } from "./useItineraryActions";

/**
 * 範例日程（GLOSSARY「範例日程」）：demo 與驗收「推薦的在意事項」用，是範例資料的唯一來源。
 * 各份的目的、預期前 3 名見 docs/demo/concern-suggestion-itineraries.md。
 * 座標是撰寫時查好的，載入時不呼叫 geocode；旅遊日程沒有交通分類，航班用 explore。
 */
export type DemoActivity = {
  /** 第幾天，1 起算。 */
  day: number;
  hour: number;
  title: string;
  location: string;
  category: ActivityCategory;
  /** 時長（小時）。 */
  duration: number;
  latitude: number;
  longitude: number;
};

export type DemoItinerary = {
  id: string;
  /** 選單文字：要看得出是範例。 */
  label: string;
  destination: Destination;
  city: string;
  days: number;
  /** 固定出發日 YYYY-MM-DD；2-2 落在颱風季（9/15）。 */
  departDate: string;
  activities: DemoActivity[];
};

const TAOYUAN = { location: "桃園國際機場", latitude: 25.0797, longitude: 121.2342 };
const KANSAI = { location: "關西國際機場", latitude: 34.432, longitude: 135.2304 };

export const DEMO_ITINERARIES: DemoItinerary[] = [
  {
    id: "main",
    label: "東京 7 天（主 demo）",
    destination: "日本",
    city: "東京",
    days: 7,
    departDate: "2026-07-05",
    activities: [
      {
        day: 1,
        hour: 13,
        title: "抵達成田機場",
        category: "explore",
        duration: 2,
        location: "成田國際機場 第 2 航廈",
        latitude: 35.7733,
        longitude: 140.3876,
      },
      {
        day: 1,
        hour: 15,
        title: "飯店放行李",
        category: "stay",
        duration: 1,
        location: "東急STAY新宿",
        latitude: 35.6909,
        longitude: 139.7062,
      },
      {
        day: 1,
        hour: 18,
        title: "思出橫丁吃串燒",
        category: "eat",
        duration: 2,
        location: "思出橫丁",
        latitude: 35.6925,
        longitude: 139.6996,
      },
      {
        day: 2,
        hour: 11,
        title: "築地場外市場吃海鮮生魚片",
        category: "eat",
        duration: 2,
        location: "築地場外市場",
        latitude: 35.6654,
        longitude: 139.7705,
      },
      {
        day: 2,
        hour: 14,
        title: "東京晴空塔",
        category: "explore",
        duration: 3,
        location: "東京晴空塔",
        latitude: 35.7101,
        longitude: 139.8107,
      },
      {
        day: 2,
        hour: 18,
        title: "阿美橫丁吃路邊攤",
        category: "eat",
        duration: 2,
        location: "阿美橫丁",
        latitude: 35.71,
        longitude: 139.7745,
      },
      {
        day: 3,
        hour: 10,
        title: "麻布台之丘",
        category: "explore",
        duration: 3,
        location: "麻布台之丘",
        latitude: 35.6615,
        longitude: 139.7408,
      },
      {
        day: 3,
        hour: 15,
        title: "增上寺",
        category: "culture",
        duration: 2,
        location: "增上寺",
        latitude: 35.6573,
        longitude: 139.7483,
      },
      {
        day: 3,
        hour: 18,
        title: "東京鐵塔看夜景",
        category: "explore",
        duration: 2,
        location: "東京鐵塔",
        latitude: 35.6584,
        longitude: 139.7455,
      },
      {
        day: 4,
        hour: 8,
        title: "河口湖・富士山一日遊（已付團費）",
        category: "explore",
        duration: 8,
        location: "河口湖",
        latitude: 35.5163,
        longitude: 138.7519,
      },
      {
        day: 5,
        hour: 10,
        title: "新宿 Sarabeth's 舒芙蕾",
        category: "eat",
        duration: 2,
        location: "Sarabeth's 新宿",
        latitude: 35.6903,
        longitude: 139.7006,
      },
      {
        day: 5,
        hour: 13,
        title: "teamLab Planets（已購票）",
        category: "explore",
        duration: 3,
        location: "teamLab Planets TOKYO",
        latitude: 35.6491,
        longitude: 139.7897,
      },
      {
        day: 5,
        hour: 21,
        title: "SHIBUYA SKY（已購票時段 21:00）",
        category: "explore",
        duration: 1,
        location: "SHIBUYA SKY",
        latitude: 35.6585,
        longitude: 139.7021,
      },
      {
        day: 6,
        hour: 9,
        title: "淺草寺",
        category: "culture",
        duration: 2,
        location: "淺草寺",
        latitude: 35.7148,
        longitude: 139.7967,
      },
      {
        day: 6,
        hour: 13,
        title: "上野恩賜公園",
        category: "explore",
        duration: 2,
        location: "上野恩賜公園",
        latitude: 35.7156,
        longitude: 139.7745,
      },
      {
        day: 6,
        hour: 16,
        title: "新宿御苑散步",
        category: "explore",
        duration: 2,
        location: "新宿御苑",
        latitude: 35.6852,
        longitude: 139.71,
      },
      {
        day: 7,
        hour: 9,
        title: "花園神社參拜",
        category: "culture",
        duration: 1,
        location: "新宿花園神社",
        latitude: 35.6933,
        longitude: 139.7058,
      },
      {
        day: 7,
        hour: 14,
        title: "搭機回台灣（14:00 成田起飛）",
        category: "explore",
        duration: 3,
        location: "成田國際機場 第 2 航廈",
        latitude: 35.7733,
        longitude: 140.3876,
      },
    ],
  },
  {
    id: "2-1",
    label: "驗收：需要緊急送醫或家屬趕來",
    destination: "日本",
    city: "京都",
    days: 4,
    departDate: "2026-12-10",
    activities: [
      { day: 1, hour: 7, title: "桃園→關西 早班機", category: "explore", duration: 3, ...TAOYUAN },
      {
        day: 2,
        hour: 9,
        title: "保津川泛舟",
        category: "explore",
        duration: 3,
        location: "京都府龜岡市 保津川下り 乘船場",
        latitude: 35.0125,
        longitude: 135.578,
      },
      {
        day: 3,
        hour: 8,
        title: "比叡山步道健行",
        category: "explore",
        duration: 5,
        location: "滋賀縣大津市 比叡山延曆寺",
        latitude: 35.0707,
        longitude: 135.8393,
      },
    ],
  },
  {
    id: "2-2",
    label: "驗收：行程取消或更改",
    destination: "日本",
    city: "大阪",
    days: 4,
    departDate: "2026-09-15",
    activities: [
      { day: 1, hour: 7, title: "桃園→關西 早班機", category: "explore", duration: 3, ...TAOYUAN },
      {
        day: 2,
        hour: 18,
        title: "嵐山懷石料理（已預付、不可退款）",
        category: "eat",
        duration: 2,
        location: "京都市右京區 嵐山",
        latitude: 35.0094,
        longitude: 135.6761,
      },
      {
        day: 3,
        hour: 19,
        title: "大阪演唱會（門票已預購）",
        category: "culture",
        duration: 3,
        location: "大阪市西區 京瓷巨蛋大阪",
        latitude: 34.6695,
        longitude: 135.4762,
      },
    ],
  },
  {
    id: "2-3",
    label: "驗收：證件遺失",
    destination: "日本",
    city: "大阪",
    days: 4,
    departDate: "2026-12-10",
    activities: [
      { day: 1, hour: 7, title: "桃園→關西 早班機", category: "explore", duration: 3, ...TAOYUAN },
      {
        day: 2,
        hour: 19,
        title: "天神祭花火大會（人潮擁擠）",
        category: "culture",
        duration: 3,
        location: "大阪市北區 天滿橋 大川",
        latitude: 34.691,
        longitude: 135.519,
      },
      {
        day: 3,
        hour: 11,
        title: "藥妝店購物退稅（出示護照）",
        category: "explore",
        duration: 3,
        location: "大阪市中央區 難波",
        latitude: 34.6655,
        longitude: 135.501,
      },
    ],
  },
  {
    id: "2-4",
    label: "驗收：行李延誤、行李遺失或損壞",
    destination: "日本",
    city: "大阪",
    days: 5,
    departDate: "2026-12-10",
    activities: [
      {
        day: 1,
        hour: 7,
        title: "桃園→香港轉機→關西（托運 2 個大行李箱）",
        category: "explore",
        duration: 6,
        ...TAOYUAN,
      },
      {
        day: 4,
        hour: 15,
        title: "買整套陶瓷餐具，托運回台",
        category: "explore",
        duration: 3,
        location: "大阪市中央區 難波高島屋",
        latitude: 34.666,
        longitude: 135.5013,
      },
      {
        day: 5,
        hour: 20,
        title: "關西→香港轉機→桃園",
        category: "explore",
        duration: 6,
        ...KANSAI,
      },
    ],
  },
];

/** 旅行規劃欄位：回程日 = 出發日 + 天數 − 1。 */
export function demoTrip(demo: DemoItinerary): TripFields {
  const depart = demo.departDate;
  return {
    destination: demo.destination,
    city: demo.city,
    depart_date: depart,
    return_date: addDays(depart, demo.days - 1),
  };
}

/** 活動日期 = 出發日 + (第幾天 − 1)。 */
export function demoActivities(demo: DemoItinerary, depart: string): ActivityIn[] {
  return demo.activities.map(({ day, ...a }) => ({ ...a, date: addDays(depart, day - 1) }));
}
