// front_end/src/components/ui/pagination-controls.tsx
"use client";

import { useMemo } from "react";
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { SearchableSelect } from "@/components/ui/searchable-select";

interface PaginationProps {
  currentPage: number;
  totalPages: number;
  pageSize: number;
  totalItems: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  pageSizeOptions?: number[];
}

const DEFAULT_PAGE_SIZES = [10, 20, 50, 100];

export function PaginationControls({
  currentPage,
  totalPages,
  pageSize,
  totalItems,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = DEFAULT_PAGE_SIZES,
}: PaginationProps) {
  
  const selectOptions = useMemo(() => {
    return pageSizeOptions.map((size) => ({
      label: size.toString(),
      value: size.toString(),
    }));
  }, [pageSizeOptions]);

  const getPageNumbers = () => {
    const pages = [];
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) pages.push(i);
    } else {
      if (currentPage <= 4) {
        pages.push(1, 2, 3, 4, 5, "...", totalPages);
      } else if (currentPage >= totalPages - 3) {
        pages.push(1, "...", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages);
      } else {
        pages.push(1, "...", currentPage - 1, currentPage, currentPage + 1, "...", totalPages);
      }
    }
    return pages;
  };

  return (
    <div className="flex flex-col sm:flex-row items-center justify-between gap-4 py-3 border-t border-zinc-800/50 text-sm text-zinc-500">
      
      {/* Left: Info & Page Size */}
      <div className="flex items-center gap-4">
        <span className="whitespace-nowrap text-xs sm:text-sm">
          Showing <span className="text-zinc-200 font-medium">{totalItems === 0 ? 0 : (currentPage - 1) * pageSize + 1}</span> - <span className="text-zinc-200 font-medium">{Math.min(currentPage * pageSize, totalItems)}</span> of <span className="text-zinc-200 font-medium">{totalItems}</span>
        </span>
        
        <div className="flex items-center gap-2">
          <span className="hidden sm:inline whitespace-nowrap text-xs">Rows:</span>
          <div className="w-[70px]">
            <SearchableSelect
              value={pageSize.toString()}
              options={selectOptions} 
              onChange={(val) => onPageSizeChange(Number(val))}
              placeholder={pageSize.toString()}
              placement="top"
              minimal={true}
              className="mb-0"
            />
          </div>
        </div>
      </div>

      {/* Right: Navigation */}
      <div className="flex items-center gap-1">
        <button
          onClick={() => onPageChange(1)}
          disabled={currentPage === 1}
          className="p-1.5 rounded hover:bg-zinc-800 disabled:opacity-30 disabled:hover:bg-transparent transition-colors text-zinc-400 hover:text-white"
        >
          <ChevronsLeft className="w-4 h-4" />
        </button>
        <button
          onClick={() => onPageChange(currentPage - 1)}
          disabled={currentPage === 1}
          className="p-1.5 rounded hover:bg-zinc-800 disabled:opacity-30 disabled:hover:bg-transparent transition-colors text-zinc-400 hover:text-white"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>

        <div className="flex items-center gap-1 mx-1">
          {getPageNumbers().map((p, idx) => (
            typeof p === "number" ? (
              <button
                key={idx}
                onClick={() => onPageChange(p)}
                className={`w-7 h-7 flex items-center justify-center rounded text-xs font-medium transition-colors
                  ${currentPage === p 
                    ? "bg-zinc-800 text-white border border-zinc-700 shadow-sm" 
                    : "hover:bg-zinc-800/50 text-zinc-500 hover:text-zinc-300"
                  }`}
              >
                {p}
              </button>
            ) : (
              <span key={idx} className="text-zinc-700 px-1 text-xs">...</span>
            )
          ))}
        </div>

        <button
          onClick={() => onPageChange(currentPage + 1)}
          disabled={currentPage === totalPages}
          className="p-1.5 rounded hover:bg-zinc-800 disabled:opacity-30 disabled:hover:bg-transparent transition-colors text-zinc-400 hover:text-white"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
        <button
          onClick={() => onPageChange(totalPages)}
          disabled={currentPage === totalPages}
          className="p-1.5 rounded hover:bg-zinc-800 disabled:opacity-30 disabled:hover:bg-transparent transition-colors text-zinc-400 hover:text-white"
        >
          <ChevronsRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}