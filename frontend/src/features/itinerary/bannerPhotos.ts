import type { Destination } from "../../client/types.gen";

/** 日程頁橫幅的背景照片，依目的地挑一張；沒列到的目的地維持純漸層。
 *  `url` 是 Unsplash 圖片網址（保留 ixid，不含尺寸參數）；`credit` 是攝影師，沒填就不顯示署名。 */
export type BannerPhoto = {
  url: string;
  credit?: { name: string; username: string };
};

const ixid = "ixlib=rb-4.1.0&ixid=NHwxMjA3fDA%3D";
const u = (id: string) => `https://images.unsplash.com/photo-${id}?${ixid}`;

// 還沒有攝影師署名（credit）；補上時加 credit: { name, username }
export const BANNER_PHOTOS: Partial<Record<Destination, BannerPhoto>> = {
  美國: { url: u("1445368794737-0cf251429ca0") },
  加拿大: { url: u("1503614472-8c93d56e92ce") },
  日本: { url: u("1545569341-9eb8b30979d9") },
  韓國: { url: u("1583833008338-31a6657917ab") },
  新加坡: { url: u("1533281808624-e9b07b4294ff") },
  英國: { url: u("1486299267070-83823f5448dd") },
  歐洲申根國家: { url: u("1773311583232-0a5765cf08ac") },
  澳洲: { url: u("1548296404-93c7694b2f91") },
  紐西蘭: { url: u("1597655601841-214a4cfe8b2c") },
  中國大陸: { url: u("1547981609-4b6bfe67ca0b") },
  香港澳門: { url: u("1502099530544-2b61cbaed85c") },
  泰國: { url: u("1714240230125-531572031bbf") },
  越南: { url: u("1504457047772-27faf1c00561") },
  馬來西亞: { url: u("1762774365051-add54197be99") },
  菲律賓: { url: u("1531761535209-180857e963b9") },
  印尼: { url: u("1604999333679-b86d54738315") },
};

/** 加上尺寸參數；ixid 要保留（Unsplash 靠它統計瀏覽）。 */
export function photoSrc(url: string, w: number, h: number): string {
  return `${url}&auto=format&fit=crop&w=${w}&h=${h}&q=80`;
}

export function creditHref(username: string): string {
  return `https://unsplash.com/@${username}?utm_source=gae265_travel_insurance&utm_medium=referral`;
}
