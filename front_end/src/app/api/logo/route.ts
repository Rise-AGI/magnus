// front_end/src/app/api/logo/route.ts

import { NextResponse } from 'next/server';
import fs from 'fs';
import path from 'path';

export async function GET() {
  try {
    const filePath = path.join(process.cwd(), '..', 'pictures', 'logo.png');
    if (!fs.existsSync(filePath)) {
      return new NextResponse("Logo not found", { status: 404 });
    }
    const fileBuffer = fs.readFileSync(filePath);
    return new NextResponse(fileBuffer, {
      headers: {
        'Content-Type': 'image/png',
        'Cache-Control': 'public, max-age=86400, immutable',
      },
    });
  } catch (e) {
    console.error("Failed to serve logo:", e);
    return new NextResponse("Internal Server Error", { status: 500 });
  }
}