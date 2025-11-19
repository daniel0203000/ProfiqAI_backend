import pandas as pd
import cloudscraper
import numpy as np
from datetime import date, timedelta, datetime
from bs4 import BeautifulSoup as bs

from util.logger import Log, Color
from util.nowtime import TaiwanTime
from util.config import Env  # 確保環境變數被載入
from util.supabase_client import supabase

scraper = cloudscraper.create_scraper()   # 防檔爬蟲用

def get_chip_data(symbol: str, start: str, end: str=TaiwanTime.string(time=False)) -> pd.DataFrame:
    """
    用於取得最新籌碼面資料。
    """
    if symbol in ("^TWII", "^TWOII"):
        Log(f"[function] get_chip_data(): 不提供籌碼面資料: {symbol}", color=Color.PURPLE)
        return pd.DataFrame()
    symbol = symbol.split(".")[0]  # 去除後綴
    url = f"https://fubon-ebrokerdj.fbs.com.tw/z/zc/zcl/zcl.djhtm?a={symbol}&c={start}&d={end}"
    scraper = cloudscraper.create_scraper()  # 使用 cloudscraper 爬取
    web = scraper.get(url).text
    bs_table = bs(web, "html.parser").find("table", class_="t01").find_all("tr")[7:-1]  # 跳過前7行和最後一行
    col = ["外資", "投信", "自營商", "三大法人合計"]
    data = []
    date_index = []
    for i in bs_table[::-1]: # 反向遍歷，因為最新的資料在最後一行
        tds = i.find_all("td")[:5]
        date = tds.pop(0).text.split('/')   # 取出日期

        texts = [td.text.strip() for td in tds]
        # 偵測是否有 '--'
        if any(text == '--' for text in texts):
            continue  # 不繼續處理這筆資料
        # 正常處理數字
        row = [int(text.replace(",", "")) for text in texts]
        data.append(row)
        # 處理日期
        date_str = f"{int(date[0])+1911}-{date[1]}-{date[2]}"
        date_index.append(date_str)
    df = pd.DataFrame(data, columns=col, index=date_index)
    return df

def get_margin_data(symbol: str, start: str, end: str=TaiwanTime.string(time=False), select_columns=None) -> pd.DataFrame:
    """
    用於取得最新融資融券資料。
    """
    # 取得網頁內容
    symbol = symbol.split(".")[0]  # 去除後綴
    url = f'https://fubon-ebrokerdj.fbs.com.tw/z/zc/zcn/zcn.djhtm?a={symbol}&c={start}&d={end}'
    scraper = cloudscraper.create_scraper()  # 使用 cloudscraper 爬取
    web = scraper.get(url).text  # 開啟網站
    bs_table = bs(web, "html.parser").find("table", class_="t01").find_all("tr")[7:-1]  # 跳過前7行和最後一行
    col = ['融資買進','融資賣出','融資現償','融資餘額','融資增減','融資限額','融資使用率%','融券賣出','融券買進','融券券償','融券餘額','融券增減','融券券資比%','資券相抵']

    def parseNum(text):
            text = text.replace(',', '').replace('%', '')
            try:
                return float(text) if '.' in text else int(text)  # 若有小數點 → 轉 float
            except ValueError:
                return text  # 保留原字串以防特殊情況
        
    data = []
    date_index = []
    for i in bs_table[::-1]:  # 反向遍歷，因為最新的資料在最後一行
        tds = i.find_all("td")
        date = tds.pop(0).text.split('/')   # 取出日期
        texts = [td.text.strip() for td in tds]
        
        # 偵測是否有 '--'
        if any(text == '--' for text in texts):
            continue  # 不繼續處理這筆資料
        # 正常處理數字
        row = [parseNum(text) for text in texts]
        data.append(row)
        # 處理日期
        date_str = f"{int(date[0])+1911}-{date[1]}-{date[2]}"
        date_index.append(date_str)
    select_columns = select_columns if select_columns else ['融資增減', '融券增減', '融券券資比%']
    df = pd.DataFrame(data, columns=col, index=date_index)[select_columns]
    return df

def main_force_all_days(stock_id, date_list):
    """
    爬取主力所有資料
    Args:
        stock_id(str): 股票代號
        date_list(list): 日期列表 ex. ['2024-05-10', '2024-05-11']
    """
    stock_id = stock_id.split('.')[0]  # 去除可能的後綴
    main_force_list = []
    # 從 Supabase 獲取已存在的主力資料
    sql_response = (
        supabase.table("stockMainForceData")
        .select("date, mainForce")
        .eq("stock_id", stock_id)
        .gte("date", date_list[0])   # 日期 >= 起始日
        .order("date")
        .execute()
    )
    sql_df = None
    sql_preupload = []

    if len(sql_response.data):
        if Env.RELOAD: print(f" Find: {stock_id} supabase 已存在！")
        sql_df = pd.DataFrame(sql_response.data).set_index("date")
        sql_df.index = pd.to_datetime(sql_df.index)

    for date in date_list:
        if Env.RELOAD: print(f"\r主力資料截取中：{date}", end="")
        # 檢查 Supabase 是否已有資料
        if (sql_df is not None) and (date in sql_df.index):
            main_force_list.append(sql_df.loc[date, "mainForce"])
            continue
        
        result = None
        while result is None:
          result = main_force_one_day(stock_id, date)
        main_force_list.append(result[0]-result[1])
        if result == (np.nan, np.nan):
            print(f"\r  Alert: {date} 無主力資料，跳過！")
            continue  # 如果沒有資料，不存入資料庫
        sql_preupload.append({
            "stock_id": stock_id,
            "date": str(date),
            "mainForce": result[0] - result[1]
        })

    # 儲存到 Supabase
    if len(sql_preupload):
        print(f"\r儲存主力資料中...{' '*15}", end="")
        response = (
            supabase.table("stockMainForceData")
            .insert(sql_preupload)
            .execute()
        )

    main_force_df = pd.DataFrame(main_force_list, columns=["主力買賣超"], index=date_list)
    
    if Env.RELOAD: print(f"\r Done: 主力資料-載入完畢！")
    return main_force_df

def main_force_one_day(stock_id, date):
    """
    main_force_all_days() 調用的輔助函數：爬取單日主力買賣超資料
    """
    try:
        url = f'https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco.djhtm?a={stock_id}&e={date}&f={date}'
        web = scraper.get(url).text
        web_bs = bs(web, 'html.parser')
        web_find = web_bs.find("tr", id="oScrollFoot")
        if web_find is None: return np.nan, np.nan  # 如果沒有資料，返回 NaN
        buysell = web_find.find_all("td", class_="t3n1")    # 買賣超 欄位
        buy_value = int(buysell[0].text.replace(",", ""))   # 買超
        sell_value = int(buysell[1].text.replace(",", ""))  # 賣超
        return buy_value, sell_value
    except Exception as e:
        print(f" \n{date} 發生錯誤：{e}")
        return None

#定義計算連續買賣超狀態
def calculate_consecutive_status(column):
    result = []
    last_status = None
    count = 0
    for num in column:
        status = 1 if num >= 0 else -1
        count = count + 1 if status == last_status else 1
        last_status = status
        result.append(status*count)
    return result

def calculate_chip_indicators(stock_id: str):
    from services.stock_data import fetchStockInfo, getStockPrice
    
    start_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")  # 最近30天資料
    stock_id, stock_name = fetchStockInfo(stock_id)
    stock_data = getStockPrice(stock_id, start_date)
    main_force_data = main_force_all_days(stock_id, stock_data.index)
    margin_data = get_margin_data(stock_id, start_date, select_columns=['融資增減', '融資餘額', '融券增減', '融券餘額', '融券券資比%'])
    df = pd.concat([stock_data, main_force_data, margin_data], axis=1, join='inner')

    df['外資連續買賣'] = calculate_consecutive_status(df['外資'])
    df['投信連續買賣'] = calculate_consecutive_status(df['投信'])
    df['自營連續買賣'] = calculate_consecutive_status(df['自營商'])
    df['主力連續買賣'] = calculate_consecutive_status(df['主力買賣超'])

    # 求出佔比
    df['外資佔比'] = (df['外資'].abs() / df['Volume']).round(4)
    df['投信佔比'] = (df['投信'].abs() / df['Volume']).round(4)
    df['自營商佔比'] = (df['自營商'].abs() / df['Volume']).round(4)
    df['主力買賣超佔比'] = (df['主力買賣超'].abs() / df['Volume']).round(4)

    df['Score'] = 0
    
    # 1. 單日買賣超
    df['Score'] += np.where(df['外資'] > 0, 1, -1)
    df['Score'] += np.where(df['投信'] > 0, 1, -1)
    df['Score'] += np.where(df['自營商'] > 0, 1, -1)
    df['Score'] += np.where(df['主力買賣超'] > 0, 1, -1)
    
    # 2. 連續買賣天數
    def score_streak(x):
        if x >= 3: return 2
        elif x <= -3: return -2
        else: return 0
    
    for c in ['外資連續買賣','投信連續買賣','自營連續買賣','主力連續買賣']:
        df['Score'] += df[c].apply(score_streak)
    
    # 3. 持股佔比
    df['Score'] += np.where(df['外資佔比'] > 0.15, 2,
                     np.where(df['外資佔比'] >= 0.05, 1, 0))
    df['Score'] += np.where(df['投信佔比'] > 0.1, 2,
                     np.where(df['投信佔比'] >= 0.05, 1, 0))
    df['Score'] += np.where(df['自營商佔比'] > 0.04, 1, 0)
    df['Score'] += np.where(df['主力買賣超佔比'] > 0.12, 2,
                     np.where(df['主力買賣超佔比'] >= 0.07, 1, 0))
    
    # 4. 融資券
    df['融資變動比'] = (df['融資增減'] / df['融資餘額']).round(4)
    df['融券變動比'] = (df['融券增減'] / df['融券餘額']).round(4)
    df['Score'] += np.where(df['融資變動比'] > 0.05, -1,
                     np.where(df['融資變動比'] > 0.02, 2,
                     np.where(df['融資變動比'] < -0.05,  -2,
                     np.where(df['融資變動比'] < -0.02,  1, 0))))
    df['Score'] += np.where(df['融券變動比'] > 0.05,  -2,
                     np.where(df['融券變動比'] > 0.02,  1,
                     np.where(df['融券變動比'] < -0.05, -1,
                     np.where(df['融券變動比'] < -0.02, 2, 0))))
    
    df['Score'] += np.where(df['融資增減'] > 0, 1, -1)
    df['Score'] += np.where(df['融券增減'] > 0, -1, 1)
    
    df['Score'] += np.where(df['融券券資比%'] < 12, 2,
                     np.where(df['融券券資比%'] <= 20, 1, -2))
    
    df['Score'] += np.where((df['融資增減'] > 0) & (df['融券增減'] < 0), 2, 0)
    df['Score'] += np.where((df['融券增減'] > 0) & (df['融資增減'] < 0), -2, 0)
    
    
    df['close_result'] = (df['Close']>df['Close'].shift(1)).astype(int)
    df['accurate'] = (((df['Score'] > 0) & (df['close_result'].shift(-1) == 1)) |((df['Score'] <= 0) & (df['close_result'].shift(-1) == 0))).astype(int)
    df = df.drop(columns=['Volume'])
    df = df.iloc[::-1]
    df['評級'] = np.select([df['Score'] > 6,df['Score'] > 1,df['Score'] > -4],['很偏多', '偏多', '偏空'],default='很偏空')
    df['direction'] = np.where(df['Score'] <= 0, -1, 1)
    latestdata = df.iloc[0]
    latestdata_dict = latestdata.to_dict()
    latestdata_dict['date'] = str(df.index[0])  # 加入日期
    return latestdata_dict
