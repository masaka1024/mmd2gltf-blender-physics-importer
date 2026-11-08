// TArray / TMap / TSet / TPair / TOptional / TSharedPtr / TFunctionRef の UE5 互換 (使われる API のみ)。
#pragma once

#include "MmdShimCore.h"
#include <algorithm>
#include <functional>
#include <map>
#include <memory>
#include <new>
#include <optional>
#include <unordered_set>

template <typename K, typename V>
struct TPair
{
	K Key;
	V Value;
	TPair() : Key(), Value() {}
	TPair(const K& InKey, const V& InValue) : Key(InKey), Value(InValue) {}
};

// ---------------------------------------------------------------------------
// TArray: 自前のバッファ (std::vector だと bool が bit 詰めになり bool& が取れないため)。
// SetNum 等で増えた要素は値初期化 (UE も零構築可能型は 0 で埋める)。
// ---------------------------------------------------------------------------
template <typename T>
class TArray
{
public:
	using ElementType = T;
	TArray() = default;
	TArray(const TArray& O) { CopyFrom(O); }
	TArray(TArray&& O) noexcept : Data_(O.Data_), Num_(O.Num_), Cap_(O.Cap_) { O.Data_ = nullptr; O.Num_ = O.Cap_ = 0; }
	TArray(std::initializer_list<T> L) { Reserve(static_cast<int32>(L.size())); for (const T& v : L) Add(v); }
	~TArray() { Destroy(); }
	TArray& operator=(const TArray& O) { if (this != &O) { Reset(); CopyFrom(O); } return *this; }
	TArray& operator=(TArray&& O) noexcept
	{
		if (this != &O) { Destroy(); Data_ = O.Data_; Num_ = O.Num_; Cap_ = O.Cap_; O.Data_ = nullptr; O.Num_ = O.Cap_ = 0; }
		return *this;
	}

	int32 Num() const { return Num_; }
	bool IsEmpty() const { return Num_ == 0; }
	T* GetData() { return Data_; }
	const T* GetData() const { return Data_; }
	bool IsValidIndex(int32 I) const { return I >= 0 && I < Num_; }
	T& operator[](int32 I) { checkf(IsValidIndex(I), "TArray index %d out of range (%d)", I, Num_); return Data_[I]; }
	const T& operator[](int32 I) const { checkf(IsValidIndex(I), "TArray index %d out of range (%d)", I, Num_); return Data_[I]; }
	T& Last(int32 IndexFromEnd = 0) { return (*this)[Num_ - 1 - IndexFromEnd]; }
	const T& Last(int32 IndexFromEnd = 0) const { return (*this)[Num_ - 1 - IndexFromEnd]; }
	T* begin() { return Data_; }
	T* end() { return Data_ + Num_; }
	const T* begin() const { return Data_; }
	const T* end() const { return Data_ + Num_; }

	void Reserve(int32 N) { if (N > Cap_) Realloc(N); }
	int32 Add(const T& Item) { T Copy(Item); return Emplace(MoveTemp(Copy)); }
	int32 Add(T&& Item) { return Emplace(MoveTemp(Item)); }
	template <typename... A> int32 Emplace(A&&... Args)
	{
		if (Num_ == Cap_) { Realloc(Cap_ < 4 ? 4 : Cap_ * 2); }
		new (Data_ + Num_) T(std::forward<A>(Args)...);
		return Num_++;
	}
	int32 AddUnique(const T& Item) { int32 I = Find(Item); return I != INDEX_NONE ? I : Add(Item); }
	void Append(const TArray& O) { Reserve(Num_ + O.Num_); for (int32 i = 0; i < O.Num_; i++) Add(O.Data_[i]); }

	// 要素を増減する。増えた分は値初期化。
	void SetNum(int32 N)
	{
		if (N < Num_) { for (int32 i = N; i < Num_; i++) Data_[i].~T(); Num_ = N; return; }
		Reserve(N);
		for (int32 i = Num_; i < N; i++) new (Data_ + i) T();
		Num_ = N;
	}
	void SetNumZeroed(int32 N) { SetNum(N); }
	void SetNumUninitialized(int32 N) { SetNum(N); }
	void Init(const T& Value, int32 N) { Reset(); Reserve(N); for (int32 i = 0; i < N; i++) Add(Value); }
	void Reset(int32 NewCap = 0) { for (int32 i = 0; i < Num_; i++) Data_[i].~T(); Num_ = 0; if (NewCap > Cap_) Realloc(NewCap); }
	void Empty(int32 Slack = 0) { Reset(Slack); }
	void RemoveAt(int32 Index, int32 Count = 1)
	{
		checkf(Count >= 0 && Index >= 0 && Index + Count <= Num_, "RemoveAt out of range");
		for (int32 i = Index; i + Count < Num_; i++) Data_[i] = MoveTemp(Data_[i + Count]);
		for (int32 i = Num_ - Count; i < Num_; i++) Data_[i].~T();
		Num_ -= Count;
	}
	int32 Remove(const T& Item)
	{
		int32 Removed = 0;
		for (int32 i = 0; i < Num_;) { if (Data_[i] == Item) { RemoveAt(i); Removed++; } else i++; }
		return Removed;
	}
	int32 Find(const T& Item) const { for (int32 i = 0; i < Num_; i++) if (Data_[i] == Item) return i; return INDEX_NONE; }
	bool Contains(const T& Item) const { return Find(Item) != INDEX_NONE; }

	// UE の Sort は introsort (非安定)。要素がポインタのときは述語に参照外しした値が渡る (UE の TDereferenceWrapper)。
	template <typename Pred> void Sort(Pred P) { SortImpl(P, std::is_pointer<T>()); }

private:
	template <typename Pred> void SortImpl(Pred& P, std::false_type) { std::sort(Data_, Data_ + Num_, [&P](const T& A, const T& B) { return P(A, B); }); }
	template <typename Pred> void SortImpl(Pred& P, std::true_type) { std::sort(Data_, Data_ + Num_, [&P](const T& A, const T& B) { return P(*A, *B); }); }
	void CopyFrom(const TArray& O) { Reserve(O.Num_); for (int32 i = 0; i < O.Num_; i++) new (Data_ + i) T(O.Data_[i]); Num_ = O.Num_; }
	void Destroy() { for (int32 i = 0; i < Num_; i++) Data_[i].~T(); ::operator delete(Data_); Data_ = nullptr; Num_ = Cap_ = 0; }
	void Realloc(int32 NewCap)
	{
		T* NewData = static_cast<T*>(::operator new(sizeof(T) * static_cast<size_t>(NewCap)));
		for (int32 i = 0; i < Num_; i++) { new (NewData + i) T(MoveTemp(Data_[i])); Data_[i].~T(); }
		::operator delete(Data_);
		Data_ = NewData; Cap_ = NewCap;
	}
	T* Data_ = nullptr;
	int32 Num_ = 0;
	int32 Cap_ = 0;
};

// ---------------------------------------------------------------------------
// TMap: 昇順の std::map で保持する (列挙順は UE では不定。コード側は順序に依存しない作り)。
// 要素は TPair<K,V> (.Key/.Value)。FString キーは FString::operator< (UE と同じ大小無視) で比較される。
// ---------------------------------------------------------------------------
template <typename K, typename V>
class TMap
{
	using Storage = std::map<K, TPair<K, V>>;
public:
	template <typename It, typename Pair>
	struct IterBase
	{
		It I;
		Pair& operator*() const { return I->second; }
		Pair* operator->() const { return &I->second; }
		IterBase& operator++() { ++I; return *this; }
		bool operator!=(const IterBase& O) const { return I != O.I; }
		bool operator==(const IterBase& O) const { return I == O.I; }
	};
	using Iter = IterBase<typename Storage::iterator, TPair<K, V>>;
	using CIter = IterBase<typename Storage::const_iterator, const TPair<K, V>>;

	int32 Num() const { return static_cast<int32>(M.size()); }
	void Reset() { M.clear(); }
	void Empty() { M.clear(); }
	V& Add(const K& Key, const V& Value) { auto R = M.insert_or_assign(Key, TPair<K, V>(Key, Value)); return R.first->second.Value; }
	V* Find(const K& Key) { auto It = M.find(Key); return It == M.end() ? nullptr : &It->second.Value; }
	const V* Find(const K& Key) const { auto It = M.find(Key); return It == M.end() ? nullptr : &It->second.Value; }
	V& FindOrAdd(const K& Key) { auto It = M.find(Key); if (It == M.end()) It = M.emplace(Key, TPair<K, V>(Key, V())).first; return It->second.Value; }
	bool Contains(const K& Key) const { return M.find(Key) != M.end(); }
	int32 Remove(const K& Key) { return static_cast<int32>(M.erase(Key)); }
	V& operator[](const K& Key) { V* P = Find(Key); checkf(P != nullptr, "TMap key not found"); return *P; }
	const V& operator[](const K& Key) const { const V* P = Find(Key); checkf(P != nullptr, "TMap key not found"); return *P; }
	Iter begin() { return Iter{ M.begin() }; }
	Iter end() { return Iter{ M.end() }; }
	CIter begin() const { return CIter{ M.begin() }; }
	CIter end() const { return CIter{ M.end() }; }
private:
	Storage M;
};

template <typename T>
class TSet
{
public:
	int32 Num() const { return static_cast<int32>(S.size()); }
	void Reset() { S.clear(); }
	void Empty() { S.clear(); }
	void Add(const T& V) { S.insert(V); }
	bool Contains(const T& V) const { return S.find(V) != S.end(); }
	int32 Remove(const T& V) { return static_cast<int32>(S.erase(V)); }
	auto begin() const { return S.begin(); }
	auto end() const { return S.end(); }
private:
	std::unordered_set<T> S;
};

// ---------------------------------------------------------------------------
template <typename T>
class TOptional
{
public:
	TOptional() = default;
	TOptional(const T& V) : O(V) {}
	TOptional(T&& V) : O(MoveTemp(V)) {}
	bool IsSet() const { return O.has_value(); }
	explicit operator bool() const { return O.has_value(); }
	T& GetValue() { checkf(O.has_value(), "TOptional unset"); return *O; }
	const T& GetValue() const { checkf(O.has_value(), "TOptional unset"); return *O; }
	T& operator*() { return GetValue(); }
	const T& operator*() const { return GetValue(); }
	T* operator->() { return &GetValue(); }
	const T* operator->() const { return &GetValue(); }
	void Reset() { O.reset(); }
	T& Emplace(const T& V) { O.emplace(V); return *O; }
private:
	std::optional<T> O;
};

template <typename T>
class TSharedPtr : public std::shared_ptr<T>
{
	using Base = std::shared_ptr<T>;
public:
	TSharedPtr() = default;
	TSharedPtr(std::nullptr_t) {}
	explicit TSharedPtr(T* P) : Base(P) {}
	TSharedPtr(const Base& B) : Base(B) {}
	template <typename U, typename = typename std::enable_if<std::is_convertible<U*, T*>::value>::type>
	TSharedPtr(const TSharedPtr<U>& O) : Base(static_cast<const std::shared_ptr<U>&>(O)) {}
	bool IsValid() const { return this->get() != nullptr; }
	T* Get() const { return this->get(); }
	void Reset() { Base::reset(); }
};
template <typename T, typename... A>
TSharedPtr<T> MakeShared(A&&... Args) { return TSharedPtr<T>(std::make_shared<T>(std::forward<A>(Args)...)); }

// 呼び出し可能オブジェクトを借りるだけ (所有しない)。
template <typename Sig> class TFunctionRef;
template <typename R, typename... A>
class TFunctionRef<R(A...)>
{
public:
	template <typename F, typename = typename std::enable_if<!std::is_same<typename std::decay<F>::type, TFunctionRef>::value>::type>
	TFunctionRef(F&& Fn) : Obj(const_cast<void*>(static_cast<const void*>(&Fn))),
		Call([](void* O, A... Args) -> R { return (*static_cast<typename std::remove_reference<F>::type*>(O))(std::forward<A>(Args)...); }) {}
	R operator()(A... Args) const { return Call(Obj, std::forward<A>(Args)...); }
private:
	void* Obj;
	R (*Call)(void*, A...);
};
