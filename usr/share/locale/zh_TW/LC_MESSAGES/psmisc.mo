��    L      |  e   �      p  `   q  b   �  p   5  k   �  #        6     S     j  )   �  	   �  3   �     �  �   	      �	  ,   �	  $   �	     
      
     <
     \
  #   {
  !   �
     �
     �
  %   �
          3     N     e     t     �     �     �  �   �  &   �     �     �     �  �     d   �     S  $   j  u   �  C     =   I     �  &   �  +   �     �  )        .  (   H  �   q  .   -  F   \  "   �  -   �     �  
          2   2  $   e  ,   �  '   �  '   �            +   "     N     c     w          �     �     �  �  �  k     b   �  n   N  r   �  $   0     U     u     �  ,   �     �  ?   �        �   ?     �  -   �  "   +     N     e  !   �     �  &   �  "   �          %  $   A     f  !   �     �     �     �     �     �  (     �   A  '        A     `     z  �   �  _   j     �  $   �  �   	  H   �  =   �     =      V  &   w     �  )   �     �  ,   �  �   (   -   �   9   �   "   9!  0   \!  %   �!     �!     �!  /   �!  (   "  *   ,"  &   W"  $   ~"     �"     �"  .   �"     �"     #  	   #  	    #     *#     1#     8#     0   K         1          G   2                  J       )   #          7   .   =              9   &                           E               F   "   +          5       '      ;   4   ?       	       8   
         A   :         !   <   /                         @         I   B   6                     -      %   ,       L      C   D   (            *      >   H   $   3      -                     reset options

  udp/tcp names: [local_port][,[rmt_host][,[rmt_port]]]

   -4,--ipv4             search IPv4 sockets only
  -6,--ipv6             search IPv6 sockets only
   -Z,--context REGEXP kill only process(es) having context
                      (must precede other arguments)
   PID    start at this PID; default is 1 (init)
  USER   show only trees rooted at processes of this user

 %*s USER        PID ACCESS COMMAND
 %s is empty (not mounted ?)
 %s: Invalid option %s
 %s: no process found
 %s: unknown signal; %s -l lists signals.
 (unknown) /proc is not mounted, cannot stat /proc/self/stat.
 Bad regular expression: %s
 CPU Times
  This Process    (user system guest blkio): %6.2f %6.2f %6.2f %6.2f
  Child processes (user system guest):       %6.2f %6.2f %6.2f
 Can't get terminal capabilities
 Cannot allocate memory for matched proc: %s
 Cannot find socket's device number.
 Cannot find user %s
 Cannot open /proc directory: %s
 Cannot open /proc/net/unix: %s
 Cannot open a network socket.
 Cannot open protocol file "%s": %s
 Cannot resolve local port %s: %s
 Cannot stat %s: %s
 Cannot stat file %s: %s
 Copyright (C) 2007 Trent Waddington

 Could not kill process %d: %s
 Error attaching to pid %i
 Invalid namespace name Invalid option Invalid time format Kill %s(%s%d) ? (y/N)  Kill process %d ? (y/N)  Killed %s(%s%d) with signal %d
 Memory
  Vsize:       %-10s
  RSS:         %-10s 		 RSS Limit: %s
  Code Start:  %#-10lx		 Code Stop:  %#-10lx
  Stack Start: %#-10lx
  Stack Pointer (ESP): %#10lx	 Inst Pointer (EIP): %#10lx
 Namespace option requires an argument. No process specification given No processes found.
 No such user name: %s
 PSmisc comes with ABSOLUTELY NO WARRANTY.
This is free software, and you are welcome to redistribute it under
the terms of the GNU General Public License.
For more information about these matters, see the files named COPYING.
 Page Faults
  This Process    (minor major): %8lu  %8lu
  Child Processes (minor major): %8lu  %8lu
 Press return to close
 Process with pid %d does not exist.
 Process, Group and Session IDs
  Process ID: %d		  Parent ID: %d
    Group ID: %d		 Session ID: %d
  T Group ID: %d

 Process: %-14s		State: %c (%s)
  CPU#:  %-3d		TTY: %s	Threads: %ld
 Scheduling
  Policy: %s
  Nice:   %ld 		 RT Priority: %ld %s
 Signal %s(%s%d) ? (y/N)  Specified filename %s does not exist.
 Specified filename %s is not a mountpoint.
 TERM is not set
 Unable to open stat file for pid %d (%s)
 Unknown local port AF %d
 Usage: killall [OPTION]... [--] NAME...
 Usage: prtstat [options] PID ...
       prtstat -V
Print information about a process
    -r,--raw       Raw display of information
    -V,--version   Display version information and exit
 You can only use files with mountpoint options You cannot search for only IPv4 and only IPv6 sockets at the same time You must provide at least one PID. all option cannot be used with silent option. asprintf in print_stat failed.
 disk sleep fuser (PSmisc) %s
 killall: %s lacks process entries (not mounted ?)
 killall: Bad regular expression: %s
 killall: Cannot get UID from process status
 killall: Maximum number of names is %d
 killall: skipping partial match %s(%d)
 paging peekfd (PSmisc) %s
 procfs file for %s namespace not available
 prtstat (PSmisc) %s
 pstree (PSmisc) %s
 running sleeping traced unknown zombie Project-Id-Version: psmisc 22.21-pre2
Report-Msgid-Bugs-To: csmall@enc.com.au
POT-Creation-Date: 2017-06-16 06:42+1000
PO-Revision-Date: 2015-10-08 12:39+0800
Last-Translator: Wei-Lun Chao <bluebat@member.fsf.org>
Language-Team: Chinese (traditional) <zh-l10n@linux.org.tw>
Language: zh_TW
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8
Content-Transfer-Encoding: 8bit
X-Bugs: Report translation errors to the Language-Team address.
Plural-Forms: nplurals=1; plural=0;
   -                     重置選項

  udp/tcp 名稱：[本地埠號][,[遠端主機][,[遠端埠號]]]

   -4,--ipv4             只搜尋 IPv4 通訊端
  -6,--ipv6             只搜尋 IPv6 通訊端
   -Z,--context REGEXP 只砍除有此上下文的行程
                     (必須位於其他引數之前)
   PID    從這個 PID 開始；預設是 1 (init)
  使用者  只顯示源自此使用者行程的樹狀結構

 %*s 使用者      PID 存取命令
 %s 是空的 (尚未掛載？)
 %s：無效的選項 %s
 %s：找不到任何行程
 %s: 不明的信號；%s -l 列出信號。
 (不明) /proc 未被掛載，無法取得 /proc/self/stat 的狀態。
 不當的正規表示式：%s
 CPU 時間
  此行程 (使用者 系統 訪客 區塊輸出入)：%6.2f %6.2f %6.2f %6.2f
  子行程 (使用者 系統 訪客)：           %6.2f %6.2f %6.2f
 無法取得終端機功能
 無法為符合的行程配置記憶體：%s
 找不到通訊端裝置編號。
 找不到使用者 %s
 無法開啟 /proc 目錄：%s
 無法開啟 /proc/net/unix：%s
 無法開啟網路通訊端。
 無法開啟協定檔案「%s」：%s
 無法解析本機通訊埠 %s:%s
 無法顯示 %s:%s
 無法顯示檔案 %s：%s
 著作權 © 2007 Trent Waddington

 無法砍除行程 %d：%s
 附加到 pid %i 時發生錯誤
 無效的名稱空間名稱 無效的選項 無效的時間格式 砍除 %s(%s%d)？(y/N)  砍除行程 %d？(y/N)  藉由信號 %4$d 砍除 %1$s(%2$s%3$d)
 記憶體
  Vsize：      %-10s
  RSS：        %-10s		 RSS 限制：%s
  程式開始：    %#-10lx		 程式停止： %#-10lx
  堆疊開始：    %#-10lx
  堆疊指標 (ESP)：%#10lx	 指令指標 (EIP)：%#10lx
 名稱空間選項需要一個引數。 沒有給定任何行程規格 找不到任何行程。
 無此類使用者名稱：%s
 PSmisc 完全不附帶任何擔保。
這是自由軟體，並且歡迎您依照 GNU 通用公共授權
來再次散布它。
請參看名為 COPYING 的檔案，以獲得更多關於這些問題的資訊。
 分頁錯誤
  此行程 (次要 主要)：%8lu  %8lu
  子行程 (次要 主要)：%8lu  %8lu
 按下輸入鍵以關閉
 具有 pid %d 的行程不存在。
 行程、群組和執行階段識別碼
    行程識別號：%d		     上層識別號：%d
    群組識別號：%d		 執行階段識別碼：%d
  T 群組識別號：%d

 行程：%-14s		狀態：%c (%s)
  CPU#: %-3d		TTY：%s	執行緒：%ld
 排程
  策略：%s
  善意：%ld 		 RT 優先權：%ld %s
 信號 %s(%s%d)？(y/N)  指定的檔名 %s 不存在。
 指定的檔名 %s 並非掛載點。
 TERM 尚未設定
 無法開啟 pid %d (%s) 的 stat 檔案
 不明的本機通訊埠 AF %d
 用法：killall [選項]… [--] 名稱…
 用法：prtstat [選項] PID…
     prtstat -V
印出行程的相關資訊
    -r,--raw       資訊的原始顯示
    -V,--version   顯示版本資訊然後離開
 您只能使用具備掛載點選項的檔案 您無法同時只搜尋 IPv4 又只搜尋 IPv6 通訊端 您必須提供至少一個 PID。 所有選項無法與安靜選項一起使用。 print_stat 中的 asprintf 失敗。
 磁碟暫停 fuser (PSmisc) %s
 killall：%s 缺乏行程條目 (未掛載？)
 killall：不當的正規表示式：%s
 killall：無法從程序狀態提取 UID
 killall：名稱數量最大值是 %d
 killall：跳過部分匹配 %s(%d)
 分頁 peekfd (PSmisc) %s
 無法使用 procfs 檔案於 %s 命名空間
 prtstat (PSmisc) %s
 pstree (PSmisc) %s
 執行中 暫停中 追蹤 不明 殭屍 