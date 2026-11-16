import type { ActivityCategory, ActivityIn, Destination } from "../../client/types.gen";
import { addDays, today } from "./tripRules";
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
  /** `next-sep-15`：下一個 9/15（颱風季）；`plus-30`：今天 +30 天。 */
  departRule: "next-sep-15" | "plus-30";
  activities: DemoActivity[];
};

const TAOYUAN = { location: "桃園國際機場", latitude: 25.0797, longitude: 121.2342 };
const KANSAI = { location: "關西國際機場", latitude: 34.432, longitude: 135.2304 };

export const DEMO_ITINERARIES: DemoItinerary[] = [
  {
    id: "main",
    label: "大阪＋京都 5 天（主 demo）",
    destination: "日本",
    city: "大阪",
    days: 5,
    departRule: "plus-30",
    activities: [
      {
        day: 1,
        hour: 7,
        title: "桃園→關西 早班機（托運 2 個大行李箱）",
        category: "explore",
        duration: 3,
        ...TAOYUAN,
      },
      {
        day: 1,
        hour: 18,
        title: "黑門市場吃生蠔、海膽、路邊攤",
        category: "eat",
        duration: 2,
        location: "大阪市中央區日本橋 黑門市場",
        latitude: 34.6624,
        longitude: 135.5063,
      },
      {
        day: 2,
        hour: 10,
        title: "京都租和服＋高級髮飾逛清水寺",
        category: "culture",
        duration: 3,
        location: "京都市東山區 清水寺",
        latitude: 34.9949,
        longitude: 135.785,
      },
      {
        day: 2,
        hour: 14,
        title: "清水燒陶藝體驗工房",
        category: "culture",
        duration: 2,
        location: "京都市東山區清水 清水燒陶藝工房",
        latitude: 34.9967,
        longitude: 135.7809,
      },
      {
        day: 2,
        hour: 17,
        title: "租電動腳踏車騎鴨川",
        category: "explore",
        duration: 2,
        location: "京都市下京區 鴨川",
        latitude: 35.0037,
        longitude: 135.7708,
      },
      {
        day: 3,
        hour: 11,
        title: "心齋橋藥妝＋電器行大採購，刷卡退稅",
        category: "explore",
        duration: 4,
        location: "大阪市中央區 心齋橋筋商店街",
        latitude: 34.6724,
        longitude: 135.5014,
      },
      {
        day: 3,
        hour: 19,
        title: "居酒屋續攤＋深夜拉麵",
        category: "eat",
        duration: 3,
        location: "大阪市中央區道頓堀",
        latitude: 34.6687,
        longitude: 135.5013,
      },
      {
        day: 4,
        hour: 10,
        title: "環球影城，App 綁卡買快速通關",
        category: "explore",
        duration: 8,
        location: "大阪市此花區 日本環球影城",
        latitude: 34.6654,
        longitude: 135.4323,
      },
      {
        day: 4,
        hour: 20,
        title: "民宿 Airbnb（借用房東的音響和腳踏車）",
        category: "stay",
        duration: 2,
        location: "大阪市浪速區日本橋",
        latitude: 34.66,
        longitude: 135.5058,
      },
      {
        day: 5,
        hour: 15,
        title: "臨空城 outlet 買易碎瓷器、再托運一箱",
        category: "explore",
        duration: 4,
        location: "泉佐野市 臨空城 Premium Outlets",
        latitude: 34.404,
        longitude: 135.312,
      },
      { day: 5, hour: 20, title: "關西→桃園 晚班機", category: "explore", duration: 3, ...KANSAI },
    ],
  },
  {
    id: "2-1",
    label: "驗收：需要緊急送醫或家屬趕來",
    destination: "日本",
    city: "京都",
    days: 4,
    departRule: "plus-30",
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
    departRule: "next-sep-15",
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
    departRule: "plus-30",
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
    departRule: "plus-30",
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

/** 下一個 9/15：今天在今年 9/15 當天或之前用今年，否則明年。 */
function nextSep15(now: string): string {
  const year = Number(now.slice(0, 4));
  return `${now <= `${year}-09-15` ? year : year + 1}-09-15`;
}

/** 旅行規劃欄位：出發日依規則推算（保留「出發日要從今天起」），回程日 = 出發日 + 天數 − 1。`now` 預設今天。 */
export function demoTrip(demo: DemoItinerary, now: string = today()): TripFields {
  const depart = demo.departRule === "next-sep-15" ? nextSep15(now) : addDays(now, 30);
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
