// front_end/src/lib/config.ts


const API_PORT = process.env.NEXT_PUBLIC_SERVER_PORT;
export const API_BASE = `http://127.0.0.1:${API_PORT}`;
export const FEISHU_APP_ID = process.env.NEXT_PUBLIC_FEISHU_APP_ID;