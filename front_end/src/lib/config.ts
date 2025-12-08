// front_end/src/lib/config.ts


// 全栈统一配置注入环境，前端通过环境变量获取后端地址
const API_PORT = process.env.SERVER_PORT;
export const API_BASE = `http://127.0.0.1:${API_PORT}`;