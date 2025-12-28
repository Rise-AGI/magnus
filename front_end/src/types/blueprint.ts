// front_end/src/types/blueprint.ts
import { User } from "@/types/auth";

export interface Blueprint {
  id: string;
  title: string;
  description: string;
  code: string;
  user_id: string;
  user?: User;
  updatedAt: string;
}