# Streamlit Cloud 배포 가이드

## 사전 준비

1. [GitHub](https://github.com) 계정
2. [Streamlit Cloud](https://share.streamlit.io) 계정 (GitHub 로그인으로 무료 가입)

---

## Step 1. GitHub 레포 준비

### `.gitignore` 생성 (프로젝트 루트)
```
data/raw/
data/processed/
__pycache__/
.idea/
*.pyc
*.xlsx
*.csv
```
> raw.xlsx는 깃허브에 올리지 않음 — Streamlit 앱에서 업로드로 제공

### `requirements.txt` 생성 (프로젝트 루트)
```
pandas
openpyxl
streamlit
plotly
```

### GitHub에 올리기
```bash
cd 프로젝트폴더
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/내아이디/레포이름.git
git push -u origin main
```

---

## Step 2. Streamlit Cloud 배포

1. [share.streamlit.io](https://share.streamlit.io) 접속
2. **"New app"** 클릭
3. 설정:
   - **Repository**: 본인 GitHub 레포 선택
   - **Branch**: `main`
   - **Main file path**: `ui/app.py`
4. **"Deploy!"** 클릭

약 1~2분 후 `https://xxx.streamlit.app` 링크 생성됩니다.

---

## Step 3. 앱 사용법 (비개발자용)

1. 링크 접속
2. 왼쪽 사이드바 **"raw.xlsx 업로드"** → 파일 선택
3. 분석 시작

> raw.xlsx는 업로드만 하면 되고, 로컬 설치 불필요

---

## 주의사항

- Streamlit Cloud 무료 플랜: **공개 레포만 가능**
  - 비공개로 하려면 유료 플랜 또는 사내 서버 배포
- 파일 업로드는 세션 동안만 유지 (새로고침 시 재업로드 필요)
- `data/raw/raw.xlsx`가 없어도 사이드바 업로드로 동작함

---

## PyCharm에서 커밋/푸시

**Terminal 탭에서:**
```bash
git add .
git commit -m "업데이트 내용"
git push
```

또는 PyCharm 상단 메뉴 **Git → Commit → Push**

Streamlit Cloud는 GitHub push 감지 시 자동 재배포됩니다.