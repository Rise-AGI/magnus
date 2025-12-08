import { User, Bell, Settings } from "lucide-react";

export function Header() {
  return (
    <header className="h-16 border-b border-zinc-800 bg-zinc-950/50 backdrop-blur sticky top-0 z-40 flex items-center justify-end px-8 gap-4">
      
      {/* Notifications */}
      <button className="p-2 text-zinc-400 hover:text-white transition-colors relative">
        <Bell className="w-4 h-4" />
        <span className="absolute top-1.5 right-1.5 w-2 h-2 bg-red-500 rounded-full"></span>
      </button>

      {/* User Profile Dropdown Placeholder */}
      <div className="flex items-center gap-3 pl-4 border-l border-zinc-800">
        <div className="text-right hidden md:block">
          <div className="text-sm font-medium text-zinc-200">Researcher</div>
          <div className="text-xs text-zinc-500">PKU-Plasma</div>
        </div>
        <div className="w-8 h-8 rounded-full bg-zinc-800 border border-zinc-700 flex items-center justify-center text-zinc-400 hover:border-blue-500 hover:text-white transition-all cursor-pointer">
          <User className="w-4 h-4" />
        </div>
      </div>
    </header>
  );
}