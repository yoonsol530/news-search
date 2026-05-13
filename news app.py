import streamlit as st
import pandas as pd
import json
import re
from google import genai
from google.genai import types
from supabase import create_client, Client

# 1. 환경 설정 및 시크릿 불러오기
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

# 클라이언트 초기화
genai_client = genai.Client(api_key=GEMINI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 페이지 설정
st.set_page_config(page_title="AI 뉴스 저장기", layout="wide")
st.title("🗞️ AI 최신 뉴스 검색 & 자동 저장기")

# 탭 구성
tab1, tab2, tab3 = st.tabs(["🔍 검색 및 저장", "📂 저장된 뉴스 보기", "📊 통계 분석"])

# --- Tab 1: 검색 및 저장 ---
with tab1:
    keyword = st.text_input("검색하고 싶은 뉴스 키워드를 입력하세요:", placeholder="예: 생성형 AI 트렌드")
    search_button = st.button("최신 뉴스 검색 시작")

    if search_button and keyword:
        with st.spinner("Gemini가 최신 정보를 검색 중입니다..."):
            try:
                # 1. Gemini Search 호출 (JSON 모드와 도구 동시 사용 불가로 텍스트 응답 유도)
                prompt = f"""
                키워드 '{keyword}'에 대한 가장 최신 뉴스 딱 2건만 검색해줘.
                결과는 반드시 아래 JSON 형식의 배열로만 응답해. 절대 다른 설명은 하지 마.
                [
                  {{"title": "기사제목", "source": "언론사", "news_date": "YYYY-MM-DD", "url": "실제URL", "summary": "3줄 요약"}}
                ]
                """
                
                response = genai_client.models.generate_content(
    model="gemini-1.5-flash",  # <--- 이 부분을 1.5로 변경!
    config=types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        temperature=0.0
    ),
    contents=prompt
)

                # 2. JSON 파싱 및 URL 환각 방지 로직
                raw_text = response.text
                # JSON 블록만 추출하는 정규식
                json_match = re.search(r'\[.*\]', raw_text, re.DOTALL)
                if json_match:
                    news_list = json.loads(json_match.group())
                    
                    # Grounding Metadata 추출
                    grounding_metadata = response.candidates[0].grounding_metadata
                    chunks = grounding_metadata.grounding_chunks if grounding_metadata else []
                    
                    final_news_data = []
                    for item in news_list:
                        real_url = item['url']
                        # Grounding Chunk를 순회하며 제목이 일치하는 실제 URL 찾기
                        if chunks:
                            for chunk in chunks:
                                if chunk.web and item['title'] in chunk.web.title:
                                    candidate_url = chunk.web.uri
                                    # redirect 링크 제외 및 http 조건 확인
                                    if "http" in candidate_url and "grounding-api-redirect" not in candidate_url:
                                        real_url = candidate_url
                                        break
                        
                        item['url'] = real_url
                        item['keyword'] = keyword
                        final_news_data.append(item)

                    # 3. 화면 출력 및 DB 저장
                    success_count = 0
                    skip_count = 0
                    
                    cols = st.columns(2)
                    for idx, news in enumerate(final_news_data):
                        with cols[idx]:
                            st.subheader(news['title'])
                            st.caption(f"{news['source']} | {news['news_date']}")
                            st.write(news['summary'])
                            st.markdown(f"[기사 읽기]({news['url']})")
                            
                            # Supabase 저장
                            try:
                                supabase.table("news_history").insert({
                                    "keyword": news['keyword'],
                                    "title": news['title'],
                                    "source": news['source'],
                                    "news_date": news['news_date'],
                                    "url": news['url'],
                                    "summary": news['summary']
                                }).execute()
                                success_count += 1
                            except Exception as e:
                                # 중복 키 에러 (23505) 체크
                                if '23505' in str(e):
                                    skip_count += 1
                                else:
                                    st.error(f"저장 중 에러 발생: {e}")

                    st.toast(f"✅ 완료! (저장: {success_count}건, 중복 제외: {skip_count}건)")
                else:
                    st.error("AI 응답에서 뉴스 데이터를 추출하지 못했습니다.")
            
            except Exception as e:
                st.error(f"오류가 발생했습니다: {e}")

# --- Tab 2: 저장된 뉴스 보기 ---
with tab2:
    st.subheader("최근 저장된 뉴스 목록")
    try:
        response = supabase.table("news_history").select("*").order("created_at", desc=True).limit(50).execute()
        if response.data:
            df = pd.DataFrame(response.data)
            # 깔끔한 출력을 위해 컬럼 선택
            st.dataframe(df[['created_at', 'keyword', 'title', 'source', 'news_date', 'url']], use_container_width=True)
            
            for _, row in df.head(10).iterrows():
                with st.expander(f"[{row['keyword']}] {row['title']}"):
                    st.write(f"**출처:** {row['source']} | **날짜:** {row['news_date']}")
                    st.write(row['summary'])
                    st.write(f"[링크 바로가기]({row['url']})")
        else:
            st.info("아직 저장된 뉴스가 없습니다.")
    except Exception as e:
        st.error(f"데이터를 불러오는 중 오류 발생: {e}")

# --- Tab 3: 통계 분석 ---
with tab3:
    st.subheader("뉴스 검색 트렌드")
    try:
        response = supabase.table("news_history").select("keyword").execute()
        if response.data:
            df_stats = pd.DataFrame(response.data)
            keyword_counts = df_stats['keyword'].value_counts()
            
            col1, col2 = st.columns([1, 2])
            with col1:
                st.write("**많이 검색된 키워드 TOP 5**")
                st.table(keyword_counts.head(5))
            with col2:
                st.bar_chart(keyword_counts)
        else:
            st.info("통계를 표시할 데이터가 부족합니다.")
    except Exception as e:
        st.error(f"통계 분석 중 오류 발생: {e}")
